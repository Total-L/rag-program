"""P1 — 持久化 + 增量向量索引。

承接 P6 的 `IndexStore`（pgvector + Chroma 双后端），
但用 **P1 自己的** chunk_id 方案，因为：

- P5 的 `Block.content_hash` 含 page_or_sheet（来自 PDF/DOCX），
  而 P1 用 `_compat.MarkdownHeadingSplitter`（自实现，见 ADR-0005），没有页码信息。
- P1 的 `chunk_id` 必须满足：同一处正文 → 同一 id（重跑无副作用），
  改一个字 → 不同的 id（让 reconcile_source 把旧的清掉）。

约定：
    chunk_id = sha256(source_id | heading_path | text).hexdigest()[:16]
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 让 P6 模块可被 import
P6_ROOT = Path(__file__).resolve().parents[2] / "p6-ingestion-platform"
if str(P6_ROOT) not in sys.path:
    sys.path.insert(0, str(P6_ROOT))

from src.store import IndexStore, VectorRecord, open_index_store  # noqa: E402


def compute_chunk_id(source_id: str, heading_path: str, text: str) -> str:
    """P1 切分方案下的稳定 chunk_id。

    三段拼接 + sha256，截前 16 hex 字符（64 位熵，10 万级去重够用）。
    分隔符用 NUL，避免 source_id="abc" 与 heading_path="bc" 这类边界混淆。
    """
    h = hashlib.sha256()
    h.update(source_id.encode("utf-8"))
    h.update(b"\x00")
    h.update(heading_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()[:16]


@dataclass
class BuildStats:
    """`build_index` 的可观测结果。"""

    total_chunks: int = 0
    new_or_changed: int = 0
    written: int = 0
    stale_removed: int = 0
    elapsed_s: float = 0.0
    by_source: dict[str, int] = field(default_factory=dict)

    def report(self) -> str:
        return (
            f"BuildStats(total={self.total_chunks} "
            f"new/changed={self.new_or_changed} "
            f"written={self.written} "
            f"stale_removed={self.stale_removed} "
            f"elapsed={self.elapsed_s:.2f}s)"
        )


class P1IndexStore:
    """P1 用的持久化 + 增量向量索引。

    设计要点：
    - **chunk_id 是稳定哈希**，所以同一个 chunk 第二次出现就是 upsert 幂等。
    - **只对 new/changed 调 embed_fn**：省 embedding 钱（这是核心收益）。
    - **每文档 reconcile_source**：编辑/删除的旧 chunk 自动清掉，避免僵尸向量。
    - **fetch_for_user(allowed_sensitivities)**：查询时按 ACL 加载，把"不在用户可见档"
      的 chunk 直接挡在内存外 —— 这是和"build 时按用户过滤"语义的对接点
      （详见 ADR-0004 / pipeline.py 注释）。
    """

    def __init__(
        self,
        *,
        backend: str = "auto",
        chroma_path: Path | str,
        collection_name: str = "p1_chunks",
        dim: int = 768,
        pg_dsn_env: str = "P1_PG_DSN",
    ):
        self.dim = dim
        # pgvector 后端的 DSN 走独立环境变量（不要复用 P6_PG_DSN，
        # 这样 P1 / P6 可以各自指向不同的库 —— 教学上更清晰）
        pg_dsn = os.environ.get(pg_dsn_env, "") or None
        # open_index_store 用 P6_PG_DSN，所以临时注入
        if pg_dsn:
            os.environ["P6_PG_DSN"] = pg_dsn
        self.store: IndexStore = open_index_store(
            backend,
            chroma_path=chroma_path,
            collection_name=collection_name,
            dim=dim,
        )

    @property
    def backend_name(self) -> str:
        return self.store.backend_name

    def build_index(
        self,
        chunks: list[dict],
        embed_fn,
    ) -> BuildStats:
        """**增量** upsert：只对真正变化的 chunk 调 embed_fn。

        Args:
            chunks: 每条是 `{"chunk_id", "source_id", "text", "sensitivity",
                              "heading_path", "embedding"?, ...}`。
                    `embedding` 已存在则视为"已有向量"，跳过重新嵌入。
            embed_fn: `texts: list[str] -> list[list[float]]`，可以是 Ollama /
                    HashEmbedder / 任何能批量出向量的实现。

        Returns:
            BuildStats：new/changed 数、写入数、对账清除的僵尸 chunk 数。
        """
        import time

        t0 = time.time()
        st = BuildStats(total_chunks=len(chunks))

        # 按 source 分桶：方便 reconcile_source（库接口是单 source 级对账）
        by_source: dict[str, list[dict]] = {}
        for c in chunks:
            by_source.setdefault(c["source_id"], []).append(c)

        # 1) 对每篇文档，找出"库里已有但本轮没出现"的 chunk → 需要删
        # 2) 对每个本轮 chunk，比对库里现有 id → 不在 = 新增，要嵌入
        to_upsert: list[dict] = []
        for sid, doc_chunks in by_source.items():
            existing = self.store.existing_chunk_ids(sid)
            current_ids = {c["chunk_id"] for c in doc_chunks}
            for c in doc_chunks:
                if c["chunk_id"] not in existing:
                    to_upsert.append(c)
            # reconcile 留到循环外：先 upsert，再对账，避免把刚写的又删掉
            st.by_source[sid] = len(doc_chunks)

        st.new_or_changed = len(to_upsert)

        # 3) 嵌入（只对新/改的）+ 写库
        if to_upsert:
            # 去重：同一 chunk_id 多次出现会触发 Chroma DuplicateIDError，
            # pgvector 的 ON CONFLICT 也会只剩一份但语义不一致；
            # 业务上重复 chunk 留一份就够（embedding / metadata 取最后一次的值）。
            seen: set[str] = set()
            unique: list[dict] = []
            for c in to_upsert:
                if c["chunk_id"] in seen:
                    continue
                seen.add(c["chunk_id"])
                unique.append(c)
            to_upsert = unique

            need_embed = [c for c in to_upsert if "embedding" not in c or c["embedding"] is None]
            already = [c for c in to_upsert if "embedding" in c and c["embedding"] is not None]
            if need_embed:
                vectors = embed_fn([c["text"] for c in need_embed])
                for c, v in zip(need_embed, vectors, strict=False):
                    c["embedding"] = v
            records = [
                VectorRecord(
                    chunk_id=c["chunk_id"],
                    source_id=c["source_id"],
                    text=c["text"],
                    embedding=c["embedding"],
                    metadata={
                        "sensitivity": c.get("sensitivity", "public"),
                        "heading_path": c.get("heading_path", ""),
                        "source_id": c["source_id"],
                        # S2 多租户：tenant_id 必须落 metadata（fetch_for_user where 过滤的前提）
                        # 缺则记为 _unassigned（与 manifest 默认对齐；生产 ingest 已强制真值）
                        "tenant_id": c.get("tenant_id", "_unassigned"),
                    },
                )
                for c in (need_embed + already)
            ]
            res = self.store.upsert(records)
            st.written = res.written

        # 4) 对账：每篇文档清掉库里多余 chunk
        # ★ current_ids 必须去重 —— 同一 chunk_id 多次出现会导致 reconcile
        # 把"应保留"的也算成"应删除"。set 语义最稳（Chroma 不会自动去重 $nin 列表）。
        for sid, doc_chunks in by_source.items():
            current_ids = sorted({c["chunk_id"] for c in doc_chunks})
            removed = self.store.reconcile_source(sid, current_ids)
            st.stale_removed += removed

        st.elapsed_s = time.time() - t0
        return st

    def fetch_for_user(
        self,
        *,
        allowed_sensitivities: list[str],
        tenant_id: str,
    ) -> list[dict]:
        """查询时按 ACL + tenant 加载该用户可见的全部 chunk（用于替换旧的 `self.index`）。

        S2 多租户：tenant_id **必填**，与 sensitivity 复合 where。
        Chroma where 顶层多字段隐式 AND —— 这是隔离 + ACL 复合的唯一守卫。
        任何路径漏传 tenant_id 都将返回空集（不是泄漏，因为没有 tenant metadata 的
        chunk 也匹配不上）—— 但调用方应主动 require_current_tenant() 提前 fail-fast。

        返回的每条 dict 形状保持与旧 `_embed_corpus` 一致：
            {"chunk_id", "source_id", "text", "embedding", "sensitivity",
             "heading_path"}

        这样 P1Pipeline 里的 dense + BM25 + RRF 检索代码不需要改动。

        代价：把所有 ACL 允许的向量一次性拉进内存（不做 ANN 排名）。
        量级（数百 ~ 数千 chunk）在 768 维下 ≈ 几 MB，对企业 KB 规模 OK。
        真上 10 万+ chunk 时必须改为每次 query 走库 —— 见 ADR-0004。
        """
        if not allowed_sensitivities:
            # 极端兜底：没有任何可见档 → 不应泄漏任何 chunk
            return []
        if not tenant_id:
            # S2 隔离：拒绝无 tenant 查询（fail-fast 而不是悄悄返回全集）
            raise ValueError("fetch_for_user requires tenant_id (S2 multi-tenancy isolation)")
        # Chroma where 必须用 $and 复合（顶层只允许一个 operator）
        rows = self.store.fetch_all(
            where={
                "$and": [
                    {"tenant_id": tenant_id},
                    {"sensitivity": {"$in": allowed_sensitivities}},
                ]
            }
        )
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "chunk_id": r.chunk_id,
                    "source_id": r.source_id,
                    "text": r.text,
                    "embedding": r.embedding,
                    "sensitivity": (r.metadata or {}).get("sensitivity", "public"),
                    "heading_path": (r.metadata or {}).get("heading_path", ""),
                    "tenant_id": (r.metadata or {}).get("tenant_id", tenant_id),
                }
            )
        return out

    def close(self) -> None:
        self.store.close()


__all__ = [
    "P1IndexStore",
    "BuildStats",
    "compute_chunk_id",
]
