"""P6 — tenacity 重试行为测试（ENTERPRISE_AUDIT D6-T1）。

覆盖：
1. retry 在第 N 次成功后停止
2. 全部失败 raise EmbeddingError
3. 指数退避时间序列
4. 不在白名单的异常不重试（透传）
5. stats["retries"] 正确累加

跑：
    pytest projects/p6-ingestion-platform/tests/test_embed_retry.py -v
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

P6_SRC = Path(__file__).resolve().parents[1] / "src"
if str(P6_SRC) not in sys.path:
    sys.path.insert(0, str(P6_SRC))

# ruff: noqa: E402 — sys.path 必须在 import embeddings 前设置
from embeddings import EmbeddingError, OllamaEmbedder  # noqa: E402


@pytest.fixture
def embedder():
    """标准 embedder 实例；max_retries=3 便于快速验证。"""
    return OllamaEmbedder(host="http://fake-host:11434", max_retries=3, cache=False, timeout=1.0)


def test_retry_succeeds_on_second_attempt(embedder):
    """第 1 次失败，第 2 次成功 → 返回成功结果，stats.retries=1（实际重试 1 次）。"""
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise urllib.error.URLError("connection refused")
        return [[0.1] * 768 for _ in texts]

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        vecs = embedder._embed_batch(["hello", "world"])

    assert len(vecs) == 2
    assert vecs[0] == pytest.approx([0.1] * 768)
    assert call_count["n"] == 2
    assert embedder.stats["retries"] == 1
    assert embedder.stats["requests"] == 1


def test_retry_succeeds_on_third_attempt(embedder):
    """第 2 次失败，第 3 次成功 → retries=2（实际重试 2 次）。"""
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise TimeoutError("slow")
        return [[0.2] * 768 for _ in texts]

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        vecs = embedder._embed_batch(["test"])

    assert len(vecs) == 1
    assert call_count["n"] == 3
    assert embedder.stats["retries"] == 2


def test_retry_exhausted_raises_embedding_error(embedder):
    """重试 N 次都失败 → raise EmbeddingError（保留因果链）。

    retries = 实际重试次数 = max_retries - 1 = 2（attempt 1 是首次尝试不计 retry）。
    """
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        raise OSError(f"attempt {call_count['n']} failed")

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        with pytest.raises(EmbeddingError, match="重试 3 次后仍失败"):
            embedder._embed_batch(["x"])

    assert call_count["n"] == 3
    assert embedder.stats["retries"] == 2  # 实际重试了 2 次
    assert embedder.stats["requests"] == 0  # 全失败，requests 不增


def test_retry_does_not_retry_non_whitelisted_exception(embedder):
    """白名单外的异常（KeyError / TypeError）不重试，立刻透传。"""
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        raise KeyError("not in whitelist")

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        with pytest.raises(KeyError):
            embedder._embed_batch(["x"])

    # tenacity retry_if_exception_type 仅匹配白名单；KeyError 不在 → 不重试
    assert call_count["n"] == 1
    assert embedder.stats["retries"] == 0


def test_retry_value_error_is_whitelisted(embedder):
    """ValueError 在白名单（如 Ollama 返回条数不匹配）→ 重试。"""
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise ValueError("return length mismatch")
        return [[0.3] * 768 for _ in texts]

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        embedder._embed_batch(["x"])

    assert call_count["n"] == 2
    assert embedder.stats["retries"] == 1


def test_retry_uses_exponential_backoff(embedder, monkeypatch):
    """指数退避：multiplier=0.5 → 第 1 次 0.5s / 第 2 次 1.0s（max=10.0）。"""
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)

    # tenacity 内部调 `time.sleep`；monkeypatch 顶层 time 模块
    import time

    monkeypatch.setattr(time, "sleep", fake_sleep)

    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise TimeoutError("flaky")
        return [[0.4] * 768 for _ in texts]

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        embedder._embed_batch(["x"])

    # tenacity wait_exponential(multiplier=0.5)：第 1 次重试前等 0.5s，第 2 次前等 1.0s
    assert len(sleep_calls) == 2
    assert sleep_calls[0] == pytest.approx(0.5)
    assert sleep_calls[1] == pytest.approx(1.0)


def test_retry_max_attempts_one_means_no_retry():
    """max_retries=1 → 失败不重试，立刻抛 EmbeddingError。"""
    emb = OllamaEmbedder(host="http://fake:11434", max_retries=1, cache=False)
    call_count = {"n": 0}

    def fake_post(texts):
        call_count["n"] += 1
        raise OSError("permanent fail")

    with patch.object(emb, "_post_embed", side_effect=fake_post):
        with pytest.raises(EmbeddingError):
            emb._embed_batch(["x"])

    assert call_count["n"] == 1
    assert emb.stats["retries"] == 0


def test_retry_propagates_original_exception_type(embedder):
    """tenacity reraise=True → 最后一次异常原样上抛（OSError 而非 EmbeddingError）。

    实际代码外层会包 EmbeddingError — 见 test_retry_exhausted_raises_embedding_error。
    这里验证 tenacity 自己的 reraise 行为是否生效。
    """
    # tenacity 默认 reraise=False（包装成 RetryError）；我们显式 reraise=True。
    # 验证：最后一次原始异常直接抛
    def fake_post(texts):
        raise TimeoutError("original timeout")

    with patch.object(embedder, "_post_embed", side_effect=fake_post):
        # tenacity reraise=True 时，最后一次抛原始 TimeoutError（外层 EmbeddingError 包裹）
        with pytest.raises(EmbeddingError) as ei:
            embedder._embed_batch(["x"])
    # 因果链：from OSError/TimeoutError
    assert isinstance(ei.value.__cause__, TimeoutError)


def test_embed_batch_success_does_not_increment_retries(embedder):
    """首次成功 → retries=0 / requests=1。"""
    with patch.object(
        embedder,
        "_post_embed",
        return_value=[[0.5] * 768, [0.5] * 768, [0.5] * 768],
    ):
        vecs = embedder._embed_batch(["a", "b", "c"])
    assert len(vecs) == 3
    assert embedder.stats["retries"] == 0
    assert embedder.stats["requests"] == 1
    assert embedder.stats["embedded"] == 3
