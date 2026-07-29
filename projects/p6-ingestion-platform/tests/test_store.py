"""P6 向量库写路径单测 —— 审计两条 critical 硬伤的回归测试。

覆盖意图（不是"函数返回了个值"，而是业务语义）：
1. upsert **幂等**：同一批重跑，行数不变（原 P5 是 append-only）
2. reconcile **清僵尸**：文档编辑后失效 chunk 必须消失
3. delete_by_source：源侧删除能真正删干净
4. metadata 过滤：ACL 过滤能生效
5. pgvector SQL 生成正确（**脱库单测**，本机无 Postgres 也能验）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.store import (  # noqa: E402
    ChromaIndexStore,
    VectorRecord,
    open_index_store,
    pg_ddl,
    pg_delete_by_source_sql,
    pg_query_sql,
    pg_reconcile_sql,
    pg_upsert_sql,
)


def _vr(cid: str, sid: str, txt: str, vec: list[float], sens: str = "internal") -> VectorRecord:
    return VectorRecord(
        chunk_id=cid,
        source_id=sid,
        text=txt,
        embedding=vec,
        metadata={"source_id": sid, "chunk_id": cid, "sensitivity": sens},
    )


@pytest.fixture()
def store(tmp_path: Path) -> ChromaIndexStore:
    return ChromaIndexStore(tmp_path / "index", collection_name="p6_unit_test")


# ── ① upsert 幂等 ────────────────────────────────────
def test_upsert_is_idempotent(store: ChromaIndexStore):
    recs = [_vr("c1", "docA", "alpha", [1.0, 0.0, 0.0]), _vr("c2", "docA", "beta", [0.0, 1.0, 0.0])]
    store.upsert(recs)
    assert store.count() == 2
    store.upsert(recs)  # 重跑
    store.upsert(recs)  # 再重跑
    assert store.count() == 2, "upsert 必须幂等：重跑不该制造重复行"


def test_upsert_updates_content_in_place(store: ChromaIndexStore):
    """同一 chunk_id 内容变了必须**更新**，而不是被 skip 掉（原 P5 的静默丢更新）。"""
    store.upsert([_vr("c1", "docA", "旧内容", [1.0, 0.0, 0.0])])
    store.upsert([_vr("c1", "docA", "新内容", [0.0, 1.0, 0.0])])
    assert store.count() == 1
    hit = store.query([0.0, 1.0, 0.0], k=1)[0]
    assert hit["text"] == "新内容", "同 id 的内容更新必须生效"


# ── ② reconcile 清僵尸向量 ────────────────────────────
def test_reconcile_removes_stale_chunks(store: ChromaIndexStore):
    """这是审计里"改文档留永久僵尸向量"的回归测试。"""
    store.upsert(
        [
            _vr("c1", "docA", "alpha", [1.0, 0.0, 0.0]),
            _vr("c2", "docA", "beta", [0.0, 1.0, 0.0]),
            _vr("c3", "docB", "gamma", [0.0, 0.0, 1.0]),
        ]
    )
    # 模拟编辑：c2 内容变 → 新 chunk_id c2new（因为 chunk_id 含正文哈希）
    store.upsert([_vr("c2new", "docA", "beta-v2", [0.0, 0.9, 0.1])])
    assert store.count() == 4, "此刻 c2 还在 = 僵尸向量"

    removed = store.reconcile_source("docA", ["c1", "c2new"])
    assert removed == 1
    assert store.count() == 3
    assert store.existing_chunk_ids("docA") == {"c1", "c2new"}
    assert store.existing_chunk_ids("docB") == {"c3"}, "对账不得影响其它文档"


def test_reconcile_empty_list_deletes_whole_source(store: ChromaIndexStore):
    store.upsert([_vr("c1", "docA", "a", [1.0, 0.0, 0.0]), _vr("c2", "docA", "b", [0.0, 1.0, 0.0])])
    assert store.reconcile_source("docA", []) == 2
    assert store.count() == 0


# ── ③ 按源删除 ───────────────────────────────────────
def test_delete_by_source(store: ChromaIndexStore):
    store.upsert([_vr("c1", "docA", "a", [1.0, 0.0, 0.0]), _vr("c2", "docB", "b", [0.0, 1.0, 0.0])])
    assert store.delete_by_source("docB") == 1
    assert store.existing_chunk_ids("docB") == set()
    assert store.count() == 1


# ── ④ metadata 过滤（ACL 能生效）──────────────────────
def test_metadata_filter_enforces_acl(store: ChromaIndexStore):
    store.upsert(
        [
            _vr("pub", "docA", "公开内容", [1.0, 0.0, 0.0], sens="public"),
            _vr("res", "docB", "机密内容", [1.0, 0.0, 0.0], sens="restricted"),
        ]
    )
    hits = store.query([1.0, 0.0, 0.0], k=5, where={"sensitivity": "public"})
    assert hits, "过滤后应仍有结果"
    assert all(h["metadata"]["sensitivity"] == "public" for h in hits)
    assert "res" not in [h["id"] for h in hits], "restricted 不得泄漏给 public 查询"


def test_query_empty_store_returns_empty(store: ChromaIndexStore):
    assert store.query([1.0, 0.0, 0.0], k=3) == []


# ── ④b fetch_all（P1 懒加载全量）─────────────────────
def test_fetch_all_returns_all_with_no_filter(store: ChromaIndexStore):
    """P1 旗舰用 `fetch_all` 一次性把用户 ACL 允许的 chunk 全装进内存。
    这是用暴力 cosine / BM25 顶替库内 ANN 的代价 —— 接受（量级在数百~数千）。
    """
    store.upsert(
        [
            _vr("c1", "docA", "alpha", [1.0, 0.0, 0.0], sens="public"),
            _vr("c2", "docA", "beta", [0.0, 1.0, 0.0], sens="public"),
            _vr("c3", "docB", "gamma", [0.0, 0.0, 1.0], sens="restricted"),
        ]
    )
    rows = store.fetch_all()
    assert {r.chunk_id for r in rows} == {"c1", "c2", "c3"}
    assert all(r.embedding for r in rows), "懒加载需要 embedding"
    assert all(r.text for r in rows), "懒加载需要原始 text"


def test_fetch_all_filters_by_metadata(store: ChromaIndexStore):
    store.upsert(
        [
            _vr("c1", "docA", "alpha", [1.0, 0.0, 0.0], sens="public"),
            _vr("c2", "docB", "beta", [0.0, 1.0, 0.0], sens="public"),
        ]
    )
    rows = store.fetch_all(where={"source_id": "docA"})
    assert {r.source_id for r in rows} == {"docA"}
    assert {r.chunk_id for r in rows} == {"c1"}


def test_fetch_all_acl_filter_does_not_leak(store: ChromaIndexStore):
    """★ fetch_all + where={"sensitivity":"public"} 必须把 restricted 滤掉。
    这是 P1 '查询时 ACL' 的关键防线：过滤器绕过则越权数据进内存。
    """
    store.upsert(
        [
            _vr("pub", "docA", "公开内容", [1.0, 0.0, 0.0], sens="public"),
            _vr("res", "docB", "机密内容", [1.0, 0.0, 0.0], sens="restricted"),
        ]
    )
    rows = store.fetch_all(where={"sensitivity": "public"})
    assert {r.chunk_id for r in rows} == {"pub"}, f"restricted 漏出: {[r.chunk_id for r in rows]}"


# ── ⑤ pgvector SQL 生成（脱库单测）─────────────────────
def test_pg_ddl_declares_vector_and_hnsw():
    ddl = " ".join(pg_ddl("rag_chunks", 768))
    assert "CREATE EXTENSION IF NOT EXISTS vector" in ddl
    assert "vector(768)" in ddl
    assert "USING hnsw (embedding vector_cosine_ops)" in ddl, "必须建 HNSW 向量索引"
    assert "m = 16" in ddl and "ef_construction = 64" in ddl, "索引调参旋钮要显式写出"
    assert "idx_rag_chunks_source" in ddl, "按 source_id 删除/对账需要索引"


def test_pg_upsert_is_real_upsert():
    sql = pg_upsert_sql()
    assert "INSERT INTO" in sql
    assert "ON CONFLICT (chunk_id) DO UPDATE" in sql, "必须是真 upsert，不是纯 INSERT"
    assert "embedding    = EXCLUDED.embedding" in sql, "向量本身也要更新"


def test_pg_reconcile_and_delete_sql():
    assert "WHERE source_id = %s" in pg_delete_by_source_sql()
    rec = pg_reconcile_sql()
    assert "NOT (chunk_id = ANY(%s))" in rec, "对账应删除不在当前集合里的 chunk"


def test_pg_query_pushes_filters_into_sql():
    """ACL 过滤下推到数据库 —— 这是 Chroma 做不到、pgvector 的核心优势。"""
    sql = pg_query_sql("rag_chunks", ["sensitivity", "author"])
    assert "sensitivity = %s" in sql, "提列字段走 btree"
    assert "metadata->>'author' = %s" in sql, "非提列字段走 JSONB"
    assert "embedding <=> %s" in sql, "cosine distance 运算符"
    assert sql.index("WHERE") < sql.index("ORDER BY"), "过滤必须在排序前"


def test_pg_query_without_filter_has_no_where():
    assert "WHERE" not in pg_query_sql("rag_chunks", [])


# ── ⑥ 后端选择：绝不静默降级 ───────────────────────────
def test_forced_pgvector_without_dsn_raises(monkeypatch):
    monkeypatch.delenv("P6_PG_DSN", raising=False)
    with pytest.raises(RuntimeError, match="DSN"):
        open_index_store("pgvector")


def test_auto_falls_back_to_chroma_when_no_dsn(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("P6_PG_DSN", raising=False)
    s = open_index_store("auto", chroma_path=tmp_path / "i", collection_name="p6_auto_fallback")
    assert s.backend_name == "chroma"
    out = capsys.readouterr().out
    assert "P6_PG_DSN" in out, "降级必须大声告警，不能静默"


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        open_index_store("faiss")


@pytest.mark.skipif(
    "not config.getoption('--run-pg', default=False)",
    reason="需要真实 Postgres+pgvector（本机无 Docker）；用 --run-pg 开启",
)
def test_pgvector_end_to_end():  # pragma: no cover - 本机无 Postgres
    """真实 pgvector 端到端。**本机未验证**，留给有 Postgres 的环境。"""
    import os

    dsn = os.environ.get("P6_PG_DSN")
    assert dsn, "设 P6_PG_DSN"
    from src.store import PgVectorIndexStore

    st = PgVectorIndexStore(dsn, table="rag_chunks_test", dim=3)
    st.upsert([_vr("c1", "docA", "alpha", [1.0, 0.0, 0.0])])
    st.upsert([_vr("c1", "docA", "alpha", [1.0, 0.0, 0.0])])
    assert st.count() == 1, "pgvector upsert 必须幂等"
    st.delete_by_source("docA")
    assert st.count() == 0
    st.close()
