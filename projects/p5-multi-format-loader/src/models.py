"""P5 — 多格式文档提取的数据模型。

设计原则：
- Block 是最小检索单元（不是 page / sheet），这样表格/图片可独立被召回
- content_hash 用于 Chroma 去重 + 增量索引
- 统一 metadata schema 让 hybrid 检索能跨格式
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class BlockType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    HEADING = "heading"


class SourceFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    HTML = "html"
    MD = "md"


@dataclass(frozen=True)
class Block:
    """统一 block —— 检索 / Chroma 写入 / ground truth 比对的最小单位。"""

    block_id: str
    source_path: str  # 原始文件路径
    source_format: SourceFormat
    block_type: BlockType
    text: str  # 文本内容（表格转 markdown，图片存 OCR 文本）
    page_or_sheet: int | str  # PDF 页码 / Excel sheet 名 / Word section / HTML 段号
    # 表格专属
    headers: tuple[str, ...] = field(default_factory=tuple)  # 表头（表格才有）
    rows: tuple[tuple[str, ...], ...] = field(default_factory=tuple)  # 表格行
    # 图片专属
    image_caption: str = ""  # 上下文推断的标题
    ocr_text: str = ""  # OCR 抽出的文字
    # 通用
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """用于 Chroma 去重 + 增量索引。"""
        h = hashlib.sha256()
        h.update(self.source_path.encode())
        h.update(str(self.page_or_sheet).encode())
        h.update(self.block_type.value.encode())
        h.update(self.text.encode())
        if self.headers:
            h.update("|".join(self.headers).encode())
        return h.hexdigest()[:16]

    def to_chroma_metadata(self) -> dict[str, Any]:
        """Chroma metadata（必须 str/int/float/bool，不能嵌套 list）。"""
        md: dict[str, Any] = {
            "block_id": self.block_id,
            "source_path": self.source_path,
            "source_format": self.source_format.value,
            "block_type": self.block_type.value,
            "page_or_sheet": str(self.page_or_sheet),
            "content_hash": self.content_hash,
        }
        if self.headers:
            md["headers"] = "|".join(self.headers)
        if self.image_caption:
            md["image_caption"] = self.image_caption[:200]
        for k, v in self.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                md[k] = v
        return md


@dataclass(frozen=True)
class Document:
    """一份原始文档解析后的产出。"""

    source_path: str
    source_format: SourceFormat
    blocks: tuple[Block, ...]
    parse_warnings: tuple[str, ...] = field(default_factory=tuple)

    def by_type(self, t: BlockType) -> list[Block]:
        return [b for b in self.blocks if b.block_type == t]


class ExtractionError(Exception):
    """格式不支持 / 解析失败时抛。"""


def _id_for(path: Path, page_or_sheet: int | str, btype: BlockType, idx: int) -> str:
    """生成稳定 block_id：路径 + 位置 + 类型 + 序号。"""
    safe = path.name.replace(".", "_").replace(" ", "_")
    return f"{safe}::{page_or_sheet}::{btype.value}::{idx:03d}"
