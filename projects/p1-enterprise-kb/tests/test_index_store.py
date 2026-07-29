"""P1 — 持久化 + 增量向量索引回归测试。

覆盖：
1. `index_corpus` 幂等：第二次跑同语料 = 0 重嵌
2. 编辑文档自动清僵尸 chunk
3. 进程重启不重嵌（新 P1Pipeline 实例从持久库加载）
4. ACL 在 query 期生效：低角色看不到高敏感度 chunk
"""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: E402
from acl import User  # noqa: E402
from index_store import P1IndexStore, compute_chunk_id  # noqa: E402


# ── 工具 ──────────────────────────────────────────────
def _fake_embed(texts: list[str]) -> list[list[float]]:
    """确定性假嵌入（基于词哈希，64 维）。"""
    out = []
    for t in texts:
        v = [0.0] * 64
        for tok in t.split():
            v[hash(tok) % 64] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture()
def fresh_store(tmp_path: Path) -> P1IndexStore:
    """每次都新起一个 Chroma 持久目录（tmp_path 隔离）。"""
    return P1IndexStore(chroma_path=tmp_path / "chroma", dim=64)


def _chunk(
    source_id: str,
    text: str,
    *,
    sensitivity: str = "public",
    tenant_id: str = "acme",
) -> dict:
    """造一份 P1 chunk dict（与 `_chunk_docs` 输出同形）。"""
    return {
        "chunk_id": compute_chunk_id(source_id, "", text),
        "source_id": source_id,
        "doc_id": source_id,
        "text": text,
        "sensitivity": sensitivity,
        "heading_path": "",
        "tenant_id": tenant_id,
    }


# ── ① chunk_id 稳定 + 去重期望 ────────────────────────
def test_chunk_id_is_stable_for_same_text():
    a = compute_chunk_id("doc1", "## 假期", "员工每年能休 5 天年假")
    b = compute_chunk_id("doc1", "## 假期", "员工每年能休 5 天年假")
    assert a == b, "相同输入必须产出相同 id（决定幂等性）"


def test_chunk_id_changes_on_text_edit():
    a = compute_chunk_id("doc1", "## 假期", "员工每年能休 5 天年假")
    b = compute_chunk_id("doc1", "## 假期", "员工每年能休 10 天年假")
    assert a != b, "文本改了 id 必须变（让 reconcile_source 把旧的清掉）"


def test_chunk_id_changes_on_heading_or_doc():
    """改 doc_id 或 heading_path 同样要换 id —— 否则不同上下文同文本会被误并。"""
    text = "完全一样的一段正文"
    assert compute_chunk_id("docA", "", text) != compute_chunk_id("docB", "", text)
    assert compute_chunk_id("docA", "## X", text) != compute_chunk_id("docA", "## Y", text)


# ── ② 增量：第二次 build = 0 新嵌入 ─────────────────────
def test_build_index_is_incremental(fresh_store: P1IndexStore):
    chunks = [_chunk(f"d{i}", f"正文 {i}") for i in range(5)]
    s1 = fresh_store.build_index(chunks, _fake_embed)
    assert s1.written == 5
    assert s1.new_or_changed == 5
    assert s1.stale_removed == 0

    s2 = fresh_store.build_index(chunks, _fake_embed)
    assert s2.new_or_changed == 0, f"第二次必须 0 新嵌入, got {s2.new_or_changed}"
    assert s2.written == 0
    assert s2.stale_removed == 0
    assert fresh_store.store.count() == 5, "持久库行数应保持稳定"


def test_build_index_only_embeds_changed_chunks(fresh_store: P1IndexStore):
    """只改一个 chunk → 只重嵌那一个，其余 0 工作量。"""
    chunks = [_chunk(f"d{i}", f"原文 {i}") for i in range(10)]
    s1 = fresh_store.build_index(chunks, _fake_embed)
    assert s1.written == 10

    # 改第 3 篇：文本变了 → chunk_id 变了 → 视为新增；旧 id 应被对账清掉
    chunks[2] = _chunk("d2", "原文 2 (已修订)")
    s2 = fresh_store.build_index(chunks, _fake_embed)
    assert s2.new_or_changed == 1, f"只该重嵌 1 个, got {s2.new_or_changed}"
    assert s2.stale_removed == 1, f"旧 chunk 必须被清, got {s2.stale_removed}"
    assert fresh_store.store.count() == 10, "净变化应为 0（10 进 1 出）"


# ── ③ 进程重启不重嵌 ──────────────────────────────────
def test_restart_does_not_re_embed(tmp_path: Path):
    """★ ADR-0004 核心收益：持久库已存向量，新 P1IndexStore 实例能直接用。"""
    path = tmp_path / "chroma"

    s1 = P1IndexStore(chroma_path=path, dim=64)
    chunks = [_chunk(f"d{i}", f"正文 {i}") for i in range(7)]
    s1.build_index(chunks, _fake_embed)
    s1.close()

    # 模拟"进程重启"：全新实例，从同一个持久目录加载
    s2 = P1IndexStore(chroma_path=path, dim=64)
    rows = s2.fetch_for_user(allowed_sensitivities=["public"], tenant_id="acme")
    assert len(rows) == 7, f"重启后应能从持久库拉回所有 chunk, got {len(rows)}"
    assert all(r["embedding"] for r in rows), "重启后 embedding 必须能恢复"
    s2.close()


# ── ④ ACL 在 query 期生效 ──────────────────────────────
def test_fetch_for_user_filters_by_sensitivity(fresh_store: P1IndexStore):
    chunks = [
        _chunk("d1", "公开内容", sensitivity="public"),
        _chunk("d2", "内部内容", sensitivity="internal"),
        _chunk("d3", "机密内容", sensitivity="restricted"),
    ]
    fresh_store.build_index(chunks, _fake_embed)
    assert fresh_store.store.count() == 3

    intern = fresh_store.fetch_for_user(allowed_sensitivities=["public"], tenant_id="acme")
    manager = fresh_store.fetch_for_user(
        allowed_sensitivities=["public", "internal"], tenant_id="acme"
    )
    exec_ = fresh_store.fetch_for_user(
        allowed_sensitivities=["public", "internal", "restricted"], tenant_id="acme"
    )

    assert {c["chunk_id"] for c in intern} == {chunks[0]["chunk_id"]}, "intern 只该看到 public"
    assert {c["chunk_id"] for c in manager} == {chunks[0]["chunk_id"], chunks[1]["chunk_id"]}
    assert {c["chunk_id"] for c in exec_} == {c["chunk_id"] for c in chunks}, (
        "executive/admin 应看到全部"
    )


def test_fetch_for_user_empty_allowed_returns_empty(fresh_store: P1IndexStore):
    fresh_store.build_index([_chunk("d1", "x")], _fake_embed)
    assert fresh_store.fetch_for_user(allowed_sensitivities=[], tenant_id="acme") == [], (
        "没有任何可见档时必须返回空（极端兜底：不能漏数据）"
    )


# ── ⑤ 与 P1Pipeline 的 self.index 行为对接 ──────────────
def test_p1_pipeline_index_property_is_lazy_and_acl_filtered(tmp_path, monkeypatch):
    """P1Pipeline.index 是 property（懒加载），且按 ACL + tenant 过滤。"""
    from config import P1Config
    from pipeline import P1Pipeline
    from tenant import Tenant
    from tenant_context import tenant_scope

    cfg = P1Config(eval_dir=tmp_path / "eval")
    monkeypatch.setenv("RAG_PROG_EMBED_BACKEND", "hash")

    # 替换 P1Pipeline 内 store 的持久路径到 tmp
    p = P1Pipeline(user=User("u", "manager"))
    p.cfg = cfg
    p.store.store.close()
    p.store = P1IndexStore(chroma_path=tmp_path / "chroma", dim=64)
    p._embed_fn = _fake_embed

    # S2：index property 必须有 tenant context 才能查（隔离核心守卫）
    tenant = Tenant.from_plan("acme", "Acme", "free")
    with tenant_scope(tenant):
        assert p.index == [], "未 index_corpus() 时 self.index 必须为空"

        p.index_corpus(tenant_id="acme")
        n_manager = len(p.index)
        assert n_manager > 0

        # 切到 intern → 强制重载缓存 → 应当看得更少
        p.user = User("u2", "intern")
        p._index_cache = None
        n_intern = len(p.index)
        assert n_intern <= n_manager, "intern 不该看到比 manager 更多"

    p.store.close()
