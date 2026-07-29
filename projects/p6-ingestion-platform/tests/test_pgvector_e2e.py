"""pgvector 真后端端到端测试（ENTERPRISE_AUDIT B 关）。

目的：
- 验证 P6 PgVectorIndexStore 在真实 Postgres+pgvector 上 upsert / query / fetch_all /
  reconcile / delete_by_source / tenant pushdown 全部按业务契约跑通。
- 不是 mock;不是 SQL 生成单测;是真的连库、出 SQL、等 DB 回包。

与既有的 test_store.py:220 test_pgvector_end_to_end 区别：
- 那边只覆盖 1 个 happy-path;这里 8 个 case 覆盖业务契约。
- 同样的 --run-pg flag / P6_PG_DSN env/conftest.py 跳过规则(同 P6 单测约定)。

前置:
1. Postgres + pgvector 可达(容器或 native)
      pgvector/pgvector:pg16 镜像
2. DSN 通过 P6_PG_DSN 注入:
      P6_PG_DSN=postgresql://rag:rag@localhost:5432/rag
3. 库可达:
      pg_isready -h localhost -p 5432

跳过: 除非传 --run-pg CLI flag,否则 SKIP。

跑法:
    P6_PG_DSN=postgresql://rag:rag@localhost:5432/rag \\
      pytest projects/p6-ingestion-platform/tests/test_pgvector_e2e.py -v --run-pg
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.store import PgVectorIndexStore, VectorRecord  # noqa: E402


def _rec(
    source_id: str,
    text: str,
    tenant: str,
    emb: list[float],
    chunk_id: str | None = None,
) -> VectorRecord:
    return VectorRecord(
        chunk_id=chunk_id or uuid.uuid4().hex,
        source_id=source_id,
        text=text,
        embedding=emb,
        metadata={"sensitivity": "public", "tenant_id": tenant, "heading_path": "h1"},
    )


@pytest.fixture(scope="module")
def store() -> PgVectorIndexStore:
    """真实 PgVectorIndexStore 实例.每次 module 建一张唯一表避免相互污染."""
    dsn = os.environ.get("P6_PG_DSN")
    if not dsn:
        pytest.skip("设 P6_PG_DSN 才跑,例如 postgresql://rag:rag@localhost:5432/rag")
    table = f"rag_chunks_e2e_{uuid.uuid4().hex[:8]}"
    s = PgVectorIndexStore(dsn=dsn, table=table, dim=4)
    yield s
    try:
        with s.conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
    except Exception:
        pass


# ── 连通性 / schema ───────────────────────────────────────────────────────


def test_psycopg_and_pgvector_extension(store: PgVectorIndexStore):
    """已连接、pgvector 扩展可用、表已自动建好."""
    assert store.conn is not None
    with store.conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ext = cur.fetchone()
        assert ext is not None, "pgvector 扩展未装:请用 pgvector/pgvector:pg16 镜像"
        cur.execute("SELECT to_regclass(%s)::regclass::text", (store.table,))
        tbl = cur.fetchone()
        assert tbl and tbl[0] == store.table, f"表 {store.table} 未自动创建"


def test_upsert_writes_rows(store: PgVectorIndexStore):
    """upsert 10 条 → existing_chunk_ids 拿回 10 个."""
    records = [
        _rec(f"doc-A-{uuid.uuid4().hex[:6]}", f"chunk {i} body", "acme-corp", [float(i) * 0.1, 0.2, 0.3, 0.4])
        for i in range(10)
    ]
    result = store.upsert(records)
    assert result.written == 10
    cid_count = sum(len(store.existing_chunk_ids(r.source_id)) for r in records)
    assert cid_count == 10


def test_upsert_is_idempotent(store: PgVectorIndexStore):
    """同 batch 再 upsert:行数不变 (ON CONFLICT UPDATE)."""
    sid = f"doc-idem-{uuid.uuid4().hex[:6]}"
    recs = [
        _rec(sid, f"body {i}", "acme-corp", [0.1 * i, 0.2, 0.3, 0.4], chunk_id=f"idem-{i}")
        for i in range(5)
    ]
    r1 = store.upsert(recs)
    r2 = store.upsert(recs)
    assert r1.written == 5
    assert r2.written == 5  # 不是 10
    assert len(store.existing_chunk_ids(sid)) == 5


def test_query_returns_topk(store: PgVectorIndexStore):
    """ANN query: 向量越近,排名越前."""
    sid = f"doc-q-{uuid.uuid4().hex[:6]}"
    store.upsert([
        _rec(sid, "anchor body", "acme-corp", [1.0, 0.0, 0.0, 0.0], chunk_id="anchor"),
        _rec(sid, "far body", "acme-corp", [-1.0, 0.0, 0.0, 0.0], chunk_id="far"),
    ])
    # 隔离:where 限制到本测试 sid,避免被前面测试写入的数据污染全局 topk
    hits = store.query([1.0, 0.0, 0.0, 0.0], k=2, where={"source_id": sid})
    assert len(hits) == 2
    assert hits[0]["id"] == "anchor", f"近邻应排第一;实得 {hits[0]}"
    assert hits[1]["id"] == "far"
    assert hits[0]["distance"] < hits[1]["distance"]


def test_query_metadata_filter_pushdown(store: PgVectorIndexStore):
    """where 真下推到 SQL (不是 client post-filter)."""
    src = f"doc-tenant-{uuid.uuid4().hex[:6]}"
    store.upsert([
        _rec(src, "acme body", "acme-corp", [0.1, 0.2, 0.3, 0.4], chunk_id="acme-1"),
        _rec(src, "other body", "other-corp", [0.1, 0.2, 0.3, 0.4], chunk_id="other-1"),
    ])
    hits = store.query([0.1, 0.2, 0.3, 0.4], k=10, where={"tenant_id": "acme-corp"})
    ids = {h["id"] for h in hits}
    assert "acme-1" in ids
    assert "other-1" not in ids


def test_fetch_all_with_where(store: PgVectorIndexStore):
    """fetch_all 按 tenant 拉回 (业务用 P1 懒加载)."""
    recs = [
        _rec(
            f"src-fa-{uuid.uuid4().hex[:4]}",
            f"fa body {i}",
            "acme-corp" if i % 2 else "other-corp",
            [0.5, 0.5, 0.5, 0.5],
        )
        for i in range(4)
    ]
    store.upsert(recs)
    rows = store.fetch_all(where={"tenant_id": "acme-corp"})
    assert all(r.metadata.get("tenant_id") == "acme-corp" for r in rows)
    assert all(hasattr(r, "embedding") for r in rows), "fetch_all 必须带 embedding (P1 用)"


def test_delete_by_source(store: PgVectorIndexStore):
    """删除一个 source_id 的全部 chunk."""
    sid = f"src-del-{uuid.uuid4().hex[:6]}"
    recs = [
        _rec(sid, f"body {i}", "acme-corp", [0.7, 0.7, 0.7, 0.7], chunk_id=f"del-{i}")
        for i in range(3)
    ]
    store.upsert(recs)
    assert len(store.existing_chunk_ids(sid)) == 3
    removed = store.delete_by_source(sid)
    assert removed == 3
    assert store.existing_chunk_ids(sid) == set()


def test_reconcile_source_kills_orphans(store: PgVectorIndexStore):
    """文档改了一字后 rebuild:reconcile_source 删掉旧 chunk,新 chunk 留下.

    业务流程:caller 先 upsert 新的 chunk_ids,然后 reconcile_source 删掉
    任何不在新集合里的 chunk(orphans)。如果 caller 不先 upsert new
    chunks,reconcile 会把所有旧 chunk 都删 — 那是 caller 的 bug,不是 SQL bug。
    """
    sid = f"src-recon-{uuid.uuid4().hex[:6]}"
    old = [
        _rec(sid, f"old body {i}", "acme-corp", [0.1, 0.2, 0.3, 0.4], chunk_id=f"old-{i}")
        for i in range(5)
    ]
    store.upsert(old)
    assert len(store.existing_chunk_ids(sid)) == 5
    # 业务流程:先 upsert 2 个新 chunk,再 reconcile,删掉剩下未在新集合里的 old(5 个)
    new = [
        _rec(sid, f"new body {i}", "acme-corp", [0.5, 0.6, 0.7, 0.8], chunk_id=f"new-{i}")
        for i in range(2)
    ]
    store.upsert(new)
    assert len(store.existing_chunk_ids(sid)) == 7  # 5 old + 2 new
    new_ids = ["new-0", "new-1"]
    removed = store.reconcile_source(sid, new_ids)
    assert removed == 5, f"应删 5 个 old orphan(7 总 - 2 new 留下),实删 {removed}"
    assert store.existing_chunk_ids(sid) == set(new_ids)
