"""P6 — 企业级摄取平台的数据模型。

设计原则：
- `SourceRecord` 是**连接器的统一出口**：不管数据来自 Postgres / S3 / Confluence /
  文件系统，都归一成同一个信封，下游（清洗 → 脱敏 → 分块 → 嵌入 → 写库）完全不感知来源。
- provenance（溯源）字段必须在**摄取那一刻**就捕获，事后补不回来：
  谁写的、什么时候改的、源侧版本号是多少、谁有权看。
  这是 P1 审计里最大的缺口之一（原来只有 `sensitivity` 一个字段）。
- `change_key` 是增量摄取的唯一依据：优先用源侧版本标识（S3 ETag / DB updated_at /
  Confluence version），拿不到才退回内容 sha256。
  → 用源侧标识可以**不下载正文**就判断"没变"，这是能扩到百万文档的关键。
- `Chunk` 与 P5 的 `Block` 不同层：Block 是"解析出来的版面单元"，
  Chunk 是"要写进向量库的检索单元"（可能由多个 Block 合成，也可能带父子层级）。

与 P5 的关系：连接器只负责把字节取回来，**解析仍复用 P5 的 loaders**
（`p5/src/loaders/*` + `ocr.py`），不重复实现 PDF/DOCX/XLSX/HTML 解析。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class Sensitivity(StrEnum):
    """与 `p1/src/acl.py:Sensitivity` 对齐的三档敏感度（这里用 str 便于落库/JSON）。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


# 数值等级：与 p1 acl.ROLE_SENSITIVITY 的比较语义保持一致（值越大越严格）
SENSITIVITY_LEVEL: dict[str, int] = {"public": 1, "internal": 2, "restricted": 3}


class ChangeType(StrEnum):
    """增量摄取的四种判定结果。"""

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    DELETED = "deleted"


@dataclass(frozen=True)
class SourceRecord:
    """连接器的统一产出 —— 一份"原始文档 + 完整溯源信息"。

    注意 `raw` 允许为 None：增量扫描时我们**先只拉元数据**（etag/mtime）判断是否变化，
    只对 new/changed 的记录才真正下载正文（`fetch_body`）。
    """

    source_id: str  # 全局稳定唯一：f"{connector}:{native_key}"
    connector: str  # sql / s3 / confluence / fs
    uri: str  # 可溯源地址（点得开的那种）
    source_format: str  # pdf/docx/xlsx/html/md/txt —— 交给 P5 loaders 分派
    raw: bytes | None = None  # 原始字节；None = 尚未拉取正文

    # ── provenance（摄取时必须捕获，事后补不回来）──────────────────
    etag: str = ""  # 源侧版本标识（S3 ETag / DB updated_at / page version）
    mtime: float = 0.0  # 最后修改时间（epoch 秒）
    author: str = ""  # 作者 / 最后修改人
    acl_principals: tuple[str, ...] = field(default_factory=tuple)  # 可见主体（组/角色）
    sensitivity: str = Sensitivity.PUBLIC.value
    version: str = ""  # 源侧版本号（Confluence 有，S3 没有）
    language: str = ""  # 由 clean 阶段回填
    extra: dict[str, Any] = field(default_factory=dict)

    # ── 指纹 ─────────────────────────────────────────
    @property
    def sha256(self) -> str:
        """正文内容指纹。未拉正文时返回空串（不要伪造）。"""
        if self.raw is None:
            return ""
        return hashlib.sha256(self.raw).hexdigest()[:16]

    @property
    def change_key(self) -> str:
        """增量比对键：源侧 etag 优先，退回内容 sha256。

        为什么优先 etag：拿 etag 不需要下载正文，list 一次就有 →
        百万文档的日常增量扫描成本从"全量下载"降到"一次 list"。
        """
        return self.etag or self.sha256

    def with_body(self, raw: bytes) -> SourceRecord:
        """补上正文（增量流程里 list 阶段无正文，fetch 阶段才填）。"""
        return replace(self, raw=raw)

    def provenance(self) -> dict[str, Any]:
        """摊平成可落库的 metadata（向量库只吃 str/int/float/bool）。"""
        return {
            "source_id": self.source_id,
            "connector": self.connector,
            "uri": self.uri,
            "source_format": self.source_format,
            "etag": self.etag,
            "mtime": self.mtime,
            "author": self.author,
            "acl_principals": "|".join(self.acl_principals),
            "sensitivity": self.sensitivity,
            "version": self.version,
            "language": self.language,
            "content_sha256": self.sha256,
        }


@dataclass(frozen=True)
class Chunk:
    """要写进向量库的检索单元。

    `chunk_id` 是 upsert 主键，必须**稳定且内容敏感**：
    - 稳定 → 同一段文字重跑得到同一个 id（否则每次重建都在制造重复行）
    - 内容敏感 → 文字一改 id 就变，配合 `reconcile` 能删掉失效的旧行

    这正是审计发现的硬伤：P5 只有 append，改文档会留下永久僵尸向量。
    """

    chunk_id: str
    source_id: str
    text: str
    ordinal: int = 0  # 在文档内的顺序
    heading_path: str = ""  # "一级标题 > 二级标题"
    parent_id: str = ""  # 父子分块：指向更大的上下文块
    chunk_type: str = "text"  # text / table / image_ocr
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make_id(source_id: str, heading_path: str, text: str) -> str:
        """稳定 chunk_id = sha256(source_id | heading_path | text)。

        与 P5 `Block.content_hash` 同族（都取 sha256 前 16 位），但 P6/P1 的切分
        没有 page_or_sheet 概念，改用 heading_path 定位。
        """
        h = hashlib.sha256()
        h.update(source_id.encode())
        h.update(b"\x00")
        h.update(heading_path.encode())
        h.update(b"\x00")
        h.update(text.encode())
        return h.hexdigest()[:16]

    def to_metadata(self, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        """合并 chunk 自身 + 文档级 provenance，摊平成向量库 metadata。"""
        md: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "ordinal": self.ordinal,
            "heading_path": self.heading_path,
            "parent_id": self.parent_id,
            "chunk_type": self.chunk_type,
        }
        if provenance:
            md.update(provenance)
        for k, v in self.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                md[k] = v
        # S2 多租户：provenance 里的 tenant_id 必须落地（vector store where 过滤的前提）
        if provenance and "tenant_id" in provenance:
            md["tenant_id"] = provenance["tenant_id"]
        # 向量库只吃标量：兜底转字符串
        return {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in md.items()}


@dataclass
class IngestFailure:
    """进 DLQ 的一条失败记录 —— 单篇文档失败不能炸掉整轮摄取。"""

    source_id: str
    stage: str  # fetch / parse / clean / redact / chunk / embed / upsert
    error: str
    uri: str = ""
    ts: float = field(default_factory=time.time)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "stage": self.stage,
            "error": self.error,
            "uri": self.uri,
            "ts": self.ts,
        }


__all__ = [
    "Sensitivity",
    "SENSITIVITY_LEVEL",
    "ChangeType",
    "SourceRecord",
    "Chunk",
    "IngestFailure",
]
