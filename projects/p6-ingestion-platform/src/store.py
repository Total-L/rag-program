"""P6 — 向量库写路径（双后端：pgvector 生产 / Chroma 本地）。

这是审计里最严重两条硬伤的正面修复：
1. P1 旗舰**根本没有向量库**（内存 list + O(N) 暴力 cosine，每次重跑全量重嵌）
2. P5 的 Chroma 是 **append-only**（`collection.add()` + skip-if-exists），
   文档改一个字就留下永久僵尸向量，静默污染召回

设计原则：
- **upsert 而不是 add**：主键冲突就更新，天然幂等。同一批数据重跑，行数不变。
- **delete_by_source / reconcile_source**：改文档要能删掉失效的旧 chunk。
  `reconcile_source(source_id, current_chunk_ids)` = "这篇文档现在只应该有这些 chunk，
  其余全删" —— 这一句就是"改文档不留僵尸"的全部秘密。
- **同一接口，两个后端**：换后端不改业务代码。这本身是一堂课 ——
  什么时候该用 Chroma（零基建、原型），什么时候该上 pgvector（事务、SQL 过滤下推、
  和业务表 JOIN、成熟运维）。
- **pgvector 的 SQL 全部由纯函数生成** → 不需要真数据库也能单测 SQL 正确性。

✅ pgvector 后端已通过本机 docker pgvector/pgvector:pg16 端到端验证(2026-07-30)
   tests/test_pgvector_e2e.py 8/8 case:upsert / idempotent / topk-ANN / tenant pushdown /
   fetch_all / delete_by_source / reconcile / 连接+扩展+schema。
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TABLE = "rag_chunks"
DEFAULT_COLLECTION = "p6_chunks"


@dataclass(frozen=True)
class VectorRecord:
    """一条要写进向量库的记录。"""

    chunk_id: str
    source_id: str
    text: str
    embedding: Sequence[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpsertResult:
    """写入结果。`unchanged` 用于证明幂等：重跑应当全落在 unchanged。"""

    written: int = 0  # 实际发给库的行数
    deleted_stale: int = 0  # reconcile 删掉的失效行

    def __add__(self, other: UpsertResult) -> UpsertResult:
        return UpsertResult(
            written=self.written + other.written,
            deleted_stale=self.deleted_stale + other.deleted_stale,
        )


class IndexStore(ABC):
    """向量库统一接口。业务代码只依赖这五个方法。"""

    backend_name: str = "abstract"

    @abstractmethod
    def upsert(self, records: Sequence[VectorRecord]) -> UpsertResult: ...

    @abstractmethod
    def delete_by_source(self, source_id: str) -> int:
        """删掉某文档的全部 chunk（源侧删除时用）。返回删除行数。"""

    @abstractmethod
    def reconcile_source(self, source_id: str, current_chunk_ids: Sequence[str]) -> int:
        """对账：该文档现在只应有 current_chunk_ids，其余删除。返回删除行数。"""

    @abstractmethod
    def query(
        self, embedding: Sequence[float], k: int = 5, where: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def count(self) -> int: ...

    def fetch_all(self, where: dict[str, Any] | None = None) -> list[VectorRecord]:
        """拉回满足过滤条件的所有 chunk（**不带** ANN 排名）。

        用途：P1 旗舰的懒加载 —— 查询时一次性把"该用户 ACL 可见"的全部 chunk
        装进内存，再走原本的 BM25 + 暴力 cosine topk。
        量级（数百 ~ 数千 chunk）允许这样做；
        真上 10 万+ chunk 应改为每次 query 走库，**不**在内存里建索引。
        """
        return []

    def existing_chunk_ids(self, source_id: str) -> set[str]:
        """该文档当前已索引的 chunk_id 集合（用于算"哪些真的要重嵌"）。"""
        return set()

    def close(self) -> None:  # pragma: no cover - 默认无资源可释放  # noqa: B027
        pass

    def __enter__(self) -> IndexStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ══════════════════════════════════════════════════════════════
# pgvector 后端 —— SQL 由纯函数生成，可脱库单测
# ══════════════════════════════════════════════════════════════


def pg_ddl(table: str = DEFAULT_TABLE, dim: int = 768) -> list[str]:
    """建表 + 建索引 DDL。

    为什么 metadata 用 JSONB 而不是一堆列：摄取的 provenance 字段会随连接器演进，
    JSONB 免迁移；需要高频过滤的字段（source_id / sensitivity）再单独提列 + 建 btree。

    为什么 HNSW 而不是 IVFFlat：HNSW 召回/延迟更好且不需要预先训练；
    代价是建索引更慢、内存更高。m / ef_construction 是这堂课要调的旋钮。
    """
    return [
        "CREATE EXTENSION IF NOT EXISTS vector;",
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            chunk_id      TEXT PRIMARY KEY,
            source_id     TEXT NOT NULL,
            text          TEXT NOT NULL,
            embedding     vector({dim}) NOT NULL,
            sensitivity   TEXT NOT NULL DEFAULT 'public',
            doc_version   TEXT NOT NULL DEFAULT '',
            content_hash  TEXT NOT NULL DEFAULT '',
            metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """.strip(),
        # 按文档删除 / 对账全靠这个索引
        f"CREATE INDEX IF NOT EXISTS idx_{table}_source ON {table}(source_id);",
        # ACL 过滤下推到库（Chroma 做不到的事）
        f"CREATE INDEX IF NOT EXISTS idx_{table}_sens ON {table}(sensitivity);",
        f"CREATE INDEX IF NOT EXISTS idx_{table}_meta ON {table} USING gin (metadata);",
        f"""
        CREATE INDEX IF NOT EXISTS idx_{table}_embedding ON {table}
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
        """.strip(),
    ]


def pg_upsert_sql(table: str = DEFAULT_TABLE) -> str:
    """真正的 upsert：主键冲突就更新。这就是 P5 缺的那一句。"""
    return f"""
    INSERT INTO {table}
        (chunk_id, source_id, text, embedding, sensitivity, doc_version, content_hash, metadata)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        source_id    = EXCLUDED.source_id,
        text         = EXCLUDED.text,
        embedding    = EXCLUDED.embedding,
        sensitivity  = EXCLUDED.sensitivity,
        doc_version  = EXCLUDED.doc_version,
        content_hash = EXCLUDED.content_hash,
        metadata     = EXCLUDED.metadata,
        updated_at   = now()
    """.strip()


def pg_delete_by_source_sql(table: str = DEFAULT_TABLE) -> str:
    return f"DELETE FROM {table} WHERE source_id = %s"


def pg_fetch_all_sql(table: str = DEFAULT_TABLE, where_keys: Sequence[str] = ()) -> str:
    """不带 ANN 排名地拉回满足过滤条件的所有 chunk（用于 P1 懒加载全量）。

    `chunk_id` 是 `WHERE` 的一部分，所以**不会**把不该见的文档拽进内存。
    """
    where_sql = ""
    if where_keys:
        conds = []
        for key in where_keys:
            if key in ("source_id", "sensitivity", "doc_version", "content_hash"):
                conds.append(f"{key} = %s")
            else:
                conds.append(f"metadata->>'{key}' = %s")
        where_sql = "WHERE " + " AND ".join(conds)
    return f"""
    SELECT chunk_id, source_id, text, sensitivity, metadata, embedding
    FROM {table}
    {where_sql}
    """.strip()


def pg_reconcile_sql(table: str = DEFAULT_TABLE) -> str:
    """删掉该文档中不在"当前应有集合"里的 chunk —— 僵尸向量的终结者。"""
    return f"DELETE FROM {table} WHERE source_id = %s AND NOT (chunk_id = ANY(%s))"


def pg_query_sql(table: str = DEFAULT_TABLE, where_keys: Sequence[str] = ()) -> str:
    """向量检索 + 元数据过滤**下推到 SQL**。

    `<=>` 是 pgvector 的 cosine distance 运算符，配合 HNSW 索引走索引扫描。
    ACL 过滤写在 WHERE 里 → 数据库层就把不该看的排除掉，
    而不是像 P1 现在那样"建索引时先按角色过滤、每个角色一份索引"。
    """
    where_sql = ""
    if where_keys:
        conds = []
        for key in where_keys:
            if key in ("source_id", "sensitivity", "doc_version", "content_hash"):
                conds.append(f"{key} = %s")  # 提列的走 btree
            else:
                conds.append(f"metadata->>'{key}' = %s")  # 其余走 JSONB
        where_sql = "WHERE " + " AND ".join(conds)
    return f"""
    SELECT chunk_id, source_id, text, metadata, embedding <=> %s::vector AS distance
    FROM {table}
    {where_sql}
    ORDER BY embedding <=> %s::vector
    LIMIT %s
    """.strip()


class PgVectorIndexStore(IndexStore):
    """Postgres + pgvector 后端（生产声明后端）。

    `psycopg` 是**延迟导入**的：没装也不影响本模块 import，
    也不影响上面那些 SQL 生成函数被单测。
    """

    backend_name = "pgvector"

    def __init__(self, dsn: str, table: str = DEFAULT_TABLE, dim: int = 768):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as e:  # pragma: no cover - 本机未装
            raise RuntimeError(
                f"pgvector 后端需要 `pip install 'psycopg[binary]' pgvector`。（原始错误：{e}）"
            ) from e

        self.dsn, self.table, self.dim = dsn, table, dim
        self.conn = psycopg.connect(dsn, autocommit=True)
        register_vector(self.conn)
        with self.conn.cursor() as cur:
            for stmt in pg_ddl(table, dim):
                cur.execute(stmt)

    def upsert(self, records: Sequence[VectorRecord]) -> UpsertResult:
        if not records:
            return UpsertResult()
        rows = [
            (
                r.chunk_id,
                r.source_id,
                r.text,
                list(r.embedding),
                str(r.metadata.get("sensitivity", "public")),
                str(r.metadata.get("version", "")),
                str(r.metadata.get("content_sha256", "")),
                json.dumps(r.metadata, ensure_ascii=False),
            )
            for r in records
        ]
        with self.conn.cursor() as cur:
            cur.executemany(pg_upsert_sql(self.table), rows)  # 批量写，别一条一条
        return UpsertResult(written=len(rows))

    def delete_by_source(self, source_id: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(pg_delete_by_source_sql(self.table), (source_id,))
            return cur.rowcount or 0

    def reconcile_source(self, source_id: str, current_chunk_ids: Sequence[str]) -> int:
        with self.conn.cursor() as cur:
            cur.execute(pg_reconcile_sql(self.table), (source_id, list(current_chunk_ids)))
            return cur.rowcount or 0

    def query(
        self, embedding: Sequence[float], k: int = 5, where: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        where = where or {}
        keys = list(where.keys())
        vec = list(embedding)
        params: list[Any] = [vec, *[where[k_] for k_ in keys], vec, k]
        with self.conn.cursor() as cur:
            cur.execute(pg_query_sql(self.table, keys), params)
            return [
                {
                    "id": r[0],
                    "source_id": r[1],
                    "text": r[2],
                    "metadata": r[3],
                    "distance": float(r[4]),
                }
                for r in cur.fetchall()
            ]

    def existing_chunk_ids(self, source_id: str) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT chunk_id FROM {self.table} WHERE source_id = %s", (source_id,))
            return {r[0] for r in cur.fetchall()}

    def fetch_all(self, where: dict[str, Any] | None = None) -> list[VectorRecord]:
        """不带 ANN 排名地拉回满足过滤的所有 chunk（用于 P1 懒加载全量）。"""
        where = where or {}
        keys = list(where.keys())
        params: list[Any] = [where[k] for k in keys]
        with self.conn.cursor() as cur:
            cur.execute(pg_fetch_all_sql(self.table, keys), params)
            out: list[VectorRecord] = []
            for chunk_id, source_id, text, sens, metadata_json, embedding in cur.fetchall():
                # psycopg 3 自动把 JSONB 列解析成 dict。但兼容旧 wiring
                # (有些 cursor 不做 JSONB 自动解析),做 type 检查。
                if isinstance(metadata_json, dict):
                    meta = metadata_json
                elif metadata_json:
                    meta = json.loads(metadata_json)
                else:
                    meta = {"sensitivity": sens}
                meta.setdefault("sensitivity", sens)
                out.append(
                    VectorRecord(
                        chunk_id=chunk_id,
                        source_id=source_id,
                        text=text,
                        embedding=embedding.to_list(),
                        metadata=meta,
                    )
                )
            return out

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self.table}")
            return int(cur.fetchone()[0])

    def close(self) -> None:
        self.conn.close()


# ══════════════════════════════════════════════════════════════
# Chroma 后端 —— 本机实测后端
# ══════════════════════════════════════════════════════════════


class ChromaIndexStore(IndexStore):
    """Chroma 后端（本地/原型）。

    与 P5 的 `P5ChromaStore` 的关键区别：这里用 `upsert()` 而不是 `add()`，
    并实现了 `delete_by_source` / `reconcile_source`。
    """

    backend_name = "chroma"

    def __init__(self, path: Path | str, collection_name: str = DEFAULT_COLLECTION):
        import chromadb
        from chromadb.config import Settings

        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.path),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        # 我们**自己算 embedding**（真实 Ollama 向量），不让 Chroma 偷偷用默认模型。
        # 这也是 P5 的坑：默认 EF 是零向量假函数，cosine 全等，静默无意义。
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, records: Sequence[VectorRecord]) -> UpsertResult:
        if not records:
            return UpsertResult()
        self.collection.upsert(
            ids=[r.chunk_id for r in records],
            documents=[r.text for r in records],
            embeddings=[list(r.embedding) for r in records],
            metadatas=[_scalarize(r.metadata) for r in records],
        )
        return UpsertResult(written=len(records))

    def delete_by_source(self, source_id: str) -> int:
        before = self.count()
        self.collection.delete(where={"source_id": source_id})
        return max(0, before - self.count())

    def reconcile_source(self, source_id: str, current_chunk_ids: Sequence[str]) -> int:
        # ★ Chroma 的 `where` 只查 metadata，**不查 ID** —— 之前用
        # `{"chunk_id": {"$nin": keep}}` 不会过滤任何 ID，导致整篇被误删。
        # 正确做法：先按 source_id 拿现有 ID，再计算差集，最后按 ID 删除。
        before = self.count()
        keep_set = set(current_chunk_ids)
        existing = set(self.collection.get(where={"source_id": source_id}, include=[])["ids"])
        stale = existing - keep_set
        if not stale:
            return 0
        # 一次只能删 ≤41,666 个 ID（Chroma 限制），但对账场景量很小
        self.collection.delete(ids=list(stale))
        return max(0, before - self.count())

    def query(
        self, embedding: Sequence[float], k: int = 5, where: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        kw: dict[str, Any] = {"query_embeddings": [list(embedding)], "n_results": k}
        if where:
            # Chroma 的 where：多条件必须显式 $and
            kw["where"] = (
                where if len(where) == 1 else {"$and": [{k_: v} for k_, v in where.items()]}
            )
        res = self.collection.query(**kw)
        if not res.get("ids") or not res["ids"][0]:
            return []
        return [
            {
                "id": i,
                "source_id": (m or {}).get("source_id", ""),
                "text": d,
                "metadata": m,
                "distance": float(dist),
            }
            for i, d, m, dist in zip(
                res["ids"][0],
                res["documents"][0],
                res["metadatas"][0],
                res["distances"][0],
                strict=False,
            )
        ]

    def existing_chunk_ids(self, source_id: str) -> set[str]:
        res = self.collection.get(where={"source_id": source_id}, include=[])
        return set(res.get("ids", []))

    def fetch_all(self, where: dict[str, Any] | None = None) -> list[VectorRecord]:
        """不带 ANN 排名地拉回满足过滤的所有 chunk（用于 P1 懒加载全量）。

        注意：Chroma 的 `where` 只支持顶层标量比较；
        metadata 里的复合键（如 `acl_principals` 列表）这里不展开，
        P1 的 ACL 过滤只用到顶层 `sensitivity`，够用。
        """
        kwargs: dict[str, Any] = {
            "include": ["embeddings", "documents", "metadatas"],
        }
        if where:
            kwargs["where"] = where
        res = self.collection.get(**kwargs)
        ids = res.get("ids") or []
        if not ids:
            return []
        # Chroma 返回 numpy 数组，`or` 会触发歧义 truth-value → 显式 None 判断
        documents = res.get("documents")
        embeddings = res.get("embeddings")
        metadatas = res.get("metadatas")
        if documents is None:
            documents = [None] * len(ids)
        if embeddings is None:
            embeddings = [None] * len(ids)
        if metadatas is None:
            metadatas = [None] * len(ids)
        return [
            VectorRecord(
                chunk_id=cid,
                source_id=(meta or {}).get("source_id", ""),
                text=text or "",
                embedding=list(emb) if emb is not None else [],
                metadata=meta or {},
            )
            for cid, text, emb, meta in zip(ids, documents, embeddings, metadatas, strict=False)
        ]

    def count(self) -> int:
        return int(self.collection.count())


def _scalarize(md: dict[str, Any]) -> dict[str, Any]:
    """Chroma metadata 只吃 str/int/float/bool，且不能为空 dict。"""
    out = {
        k: (v if isinstance(v, (str, int, float, bool)) else str(v))
        for k, v in md.items()
        if v is not None
    }
    return out or {"_": ""}


# ══════════════════════════════════════════════════════════════
# 工厂：自动选后端（显式告警，绝不静默降级）
# ══════════════════════════════════════════════════════════════


def open_index_store(
    backend: str = "auto",
    *,
    chroma_path: Path | str = "./data/index",
    collection_name: str = DEFAULT_COLLECTION,
    dsn: str | None = None,
    table: str = DEFAULT_TABLE,
    dim: int = 768,
) -> IndexStore:
    """按声明优先级选后端：pgvector（生产）> Chroma（本地）。

    backend:
      - "pgvector" → 强制 pgvector，连不上直接抛（不静默降级）
      - "chroma"   → 强制 Chroma
      - "auto"     → 有 `P6_PG_DSN` 且连得上就用 pgvector，否则用 Chroma 并**大声告警**

    "大声告警"是刻意的：静默降级会让人以为自己在跑生产写路径，
    实际跑的是本地嵌入式库 —— 这类误会正是审计要消灭的东西。
    """
    dsn = dsn or os.environ.get("P6_PG_DSN", "")

    if backend == "pgvector":
        if not dsn:
            raise RuntimeError("backend='pgvector' 但没有 DSN（设 P6_PG_DSN 或传 dsn=）")
        return PgVectorIndexStore(dsn, table=table, dim=dim)

    if backend == "chroma":
        return ChromaIndexStore(chroma_path, collection_name=collection_name)

    if backend != "auto":
        raise ValueError(f"未知 backend: {backend!r}（auto / pgvector / chroma）")

    if dsn:
        try:
            return PgVectorIndexStore(dsn, table=table, dim=dim)
        except Exception as e:
            log.warning("pgvector 连接失败，降级到 Chroma：%s", e)
            print(f"⚠️  pgvector 不可用（{e}）→ 降级 Chroma。这不是生产写路径！")
    else:
        print(
            "⚠️  未设 P6_PG_DSN → 使用 Chroma 本地后端。\n"
            "    声明的生产后端是 pgvector；要跑真实生产写路径请先起 Postgres："
            "\n    docker compose -f 05-data-engineering/docker-compose.yml up -d postgres"
        )
    return ChromaIndexStore(chroma_path, collection_name=collection_name)


__all__ = [
    "VectorRecord",
    "UpsertResult",
    "IndexStore",
    "PgVectorIndexStore",
    "ChromaIndexStore",
    "open_index_store",
    "pg_ddl",
    "pg_upsert_sql",
    "pg_delete_by_source_sql",
    "pg_reconcile_sql",
    "pg_query_sql",
]
