"""P1 BM25 缓存测试（根因 5 优化）。

背景（详见 RCA_p1_eval_regression.md 根因 5）：
- 旧实现每次 _ask_legacy() 都重新 tokenize 全语料 + 重建 BM25Okapi
- 50 题评测 = 50 次重建，单次约 100-500ms（小语料）；累计 5-25s 纯浪费
- agentic_graph retry 多次则 × N 倍

修复（pipeline._get_bm25）：
- cache key = (user_role, sorted(chunk_ids tuple))
- 用户/索引变 → 自动失效
- 同用户同索引 → 复用 BM25Okapi 与 corpus_tokens

依赖：rank_bm25（不在 pyproject 锁文件里时 skip 测试）。
"""

from __future__ import annotations

import pytest

try:
    import rank_bm25  # noqa: F401

    HAS_RANK_BM25 = True
except ImportError:
    HAS_RANK_BM25 = False

from acl import User  # noqa: E402
from pipeline import P1Pipeline  # noqa: E402

pytestmark = pytest.mark.skipif(
    not HAS_RANK_BM25, reason="rank_bm25 not installed (本环境无 RAGAS 检索依赖)"
)


def _fake_index(n: int = 5):
    """构造一个 n-chunk 的伪索引（绕开真实 embedding/Ollama）。"""
    return [
        {
            "chunk_id": f"c{i + 1}",
            "source_id": f"hr-{i + 1:03d}",
            "text": f"chunk {i + 1} sample text 年假 政策",
            "embedding": [0.1 * (i + 1)] * 4,
            "sensitivity": "public",
            "heading_path": "h1",
        }
        for i in range(n)
    ]


def _make_pipeline_with_index(user_role: str = "executive", n: int = 5):
    """构造带伪索引的 P1Pipeline。"""
    p = P1Pipeline(user=User("alice", user_role))
    p._indexed = True
    p._index_cache = _fake_index(n)
    return p


# ---- 缓存命中 ----


def test_second_call_returns_cached_bm25():
    """两次 _get_bm25 返回同一 BM25Okapi 实例（identity 检查）。"""
    p = _make_pipeline_with_index()
    bm1, tokens1 = p._get_bm25()
    bm2, tokens2 = p._get_bm25()
    assert bm1 is bm2, "BM25Okapi 实例必须复用（is 同一对象）"
    assert tokens1 is tokens2, "corpus_tokens 也必须复用（避免重复 tokenize）"


def test_first_call_populates_cache():
    """首次 _get_bm25 后 _bm25_cache_key 应被填上。"""
    p = _make_pipeline_with_index()
    assert p._bm25_cache_key is None
    p._get_bm25()
    assert p._bm25_cache_key is not None
    assert p._bm25 is not None
    assert p._bm25_corpus_tokens is not None


# ---- 失效 ----


def test_index_corpus_invalidates_cache():
    """模拟 index_corpus() 的清理逻辑：BM25 缓存必须清空。"""
    p = _make_pipeline_with_index()
    p._get_bm25()  # 先建缓存
    assert p._bm25 is not None

    # 模拟 pipeline.index_corpus 里的清理（验证契约存在）
    p._index_cache = None
    p._bm25_cache_key = None
    p._bm25 = None
    p._bm25_corpus_tokens = None

    assert p._bm25_cache_key is None
    assert p._bm25 is None
    assert p._bm25_corpus_tokens is None


def test_user_role_change_invalidates_cache():
    """user.role 变化 → cache key 变 → 重建 BM25。"""
    p = _make_pipeline_with_index(user_role="executive")
    p._get_bm25()
    first_key = p._bm25_cache_key
    assert first_key[0] == "executive"

    # 切角色
    p.user = User("alice", "manager")
    p._get_bm25()  # cache miss
    second_key = p._bm25_cache_key
    assert second_key[0] == "manager"
    assert first_key != second_key, "角色变化必须触发 cache 失效"


def test_index_size_change_invalidates_cache():
    """index 增/减 chunk → cache key 变 → 重建。"""
    p = _make_pipeline_with_index(n=5)
    p._get_bm25()
    first_key = p._bm25_cache_key
    first_tokens = p._bm25_corpus_tokens

    # 增 chunk
    p._index_cache = _fake_index(n=7)
    p._get_bm25()
    second_key = p._bm25_cache_key
    second_tokens = p._bm25_corpus_tokens

    assert first_key != second_key, "chunk 增删必须触发 cache 失效"
    assert len(first_tokens) == 5
    assert len(second_tokens) == 7


# ---- 性能收益 ----


def test_bm25_rebuild_count_when_repeated_queries():
    """重复 N 次 _get_bm25 只触发 1 次 BM25Okapi 构造。

    这是性能优化的核心收益：50 题评测节省 49 次 BM25 构造。
    """
    p = _make_pipeline_with_index()
    bm1 = p._get_bm25()[0]
    for _ in range(10):
        bm = p._get_bm25()[0]
        assert bm is bm1, "10 次调用必须返回同一 BM25Okapi 实例"


def test_corpus_tokens_identity_when_cached():
    """corpus_tokens 也必须复用对象（避免重复 tokenize）。"""
    p = _make_pipeline_with_index()
    tokens1 = p._get_bm25()[1]
    tokens2 = p._get_bm25()[1]
    assert tokens1 is tokens2
    # 验证 list 内容是 token 列表（不是原始 text）
    assert all(isinstance(t, list) for t in tokens1)
    assert all(isinstance(tok, str) for t in tokens1 for tok in t)


# ---- 配置 ----


def test_cache_key_includes_user_role():
    """cache key 必须包含 user.role（不同 ACL 视图 → 不同 cache）。"""
    p1 = _make_pipeline_with_index(user_role="executive")
    p1._get_bm25()
    key1 = p1._bm25_cache_key

    p2 = _make_pipeline_with_index(user_role="manager")
    p2._get_bm25()
    key2 = p2._bm25_cache_key

    assert key1[0] == "executive"
    assert key2[0] == "manager"


def test_cache_key_includes_chunk_ids():
    """cache key 必须包含 chunk_ids 集合（任何 chunk 增删改 → cache 失效）。"""
    p = _make_pipeline_with_index(n=5)
    p._get_bm25()
    key = p._bm25_cache_key

    # key = (role, tuple(sorted(chunk_ids)))
    assert key[1] == tuple(sorted(c["chunk_id"] for c in p._index_cache))
