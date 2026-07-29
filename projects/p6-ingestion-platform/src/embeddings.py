"""P6 — 嵌入层（批量 + 重试 + 缓存）。

审计发现的吞吐问题：P1 是"一篇文档一次 HTTP 打到 Ollama"（`app.py:56-71`），
没有批量、没有并发、没有重试。嵌入通常是整条摄取流水线里最慢的一段，
不做批量就等于把成本和时延放大几十倍。

本模块三件事：
- **批量**：一次请求打包多条（Ollama 原生 `/api/embed` 支持数组输入）
- **重试**：指数退避。嵌入服务抖一下不该让整轮摄取失败
- **内容缓存**：同一段文字不重复嵌（增量场景里命中率很高）

注意：这里刻意**不做并发**。Ollama 本地单实例并发反而会互相抢 GPU/CPU，
真要提吞吐应该横向扩服务端，而不是客户端猛开线程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

# 异常白名单：网络层 + 超时 + 上游数据错。ValueError 用于"返回条数不匹配"
# 这种业务语义错误也值得重试（Ollama 中间态返回空 / 长度漂移）。
_RETRYABLE_EXC = (
    urllib.error.URLError,
    TimeoutError,
    OSError,
    ValueError,
)

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_HOST = "http://localhost:11434"
# nomic-embed-text 的维度；换模型要同步改 pgvector 的 vector(dim)
DEFAULT_DIM = 768


class EmbeddingError(RuntimeError):
    """嵌入失败（重试耗尽后抛）。调用方应把该文档丢进 DLQ 而不是整轮崩掉。"""


class OllamaEmbedder:
    """本地 Ollama 嵌入。

    Args:
        model: 嵌入模型名
        host: Ollama 地址
        batch_size: 每次请求打包多少条
        max_retries: 单批最大重试次数
        cache: 是否按内容哈希缓存
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        batch_size: int = 32,
        max_retries: int = 3,
        timeout: float = 60.0,
        cache: bool = True,
    ):
        self.model = model
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.timeout = timeout
        self._cache: dict[str, list[float]] | None = {} if cache else None
        self.stats = {"requests": 0, "embedded": 0, "cache_hits": 0, "retries": 0}

    # ── 对外接口 ─────────────────────────────────────
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量嵌入。返回与输入等长、顺序一致的向量列表。"""
        if not texts:
            return []
        out: list[list[float] | None] = [None] * len(texts)
        todo: list[tuple[int, str]] = []

        for i, t in enumerate(texts):
            key = self._key(t)
            if self._cache is not None and key in self._cache:
                out[i] = self._cache[key]
                self.stats["cache_hits"] += 1
            else:
                todo.append((i, t))

        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            vecs = self._embed_batch([t for _, t in batch])
            for (i, t), v in zip(batch, vecs, strict=False):
                out[i] = v
                if self._cache is not None:
                    self._cache[self._key(t)] = v

        missing = [i for i, v in enumerate(out) if v is None]
        if missing:
            raise EmbeddingError(f"有 {len(missing)} 条未拿到向量（索引 {missing[:5]}…）")
        return [v for v in out if v is not None]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dim(self) -> int:
        """探测真实维度（别写死 —— 换模型就错）。"""
        return len(self.embed_query("dimension probe"))

    # ── 内部 ────────────────────────────────────────
    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """带 tenacity 重试的批量嵌入（指数退避 + 异常白名单）。

        为什么用 Retrying 对象而非 @retry 装饰器：
        - 需要在每次重试**前**累加 `self.stats["retries"]`（外部读这个 metric
          判断 embedding 服务健康度，装饰器下访问 self 较别扭）。
        - tenacity 的 `wait_exponential(multiplier=0.5, max=10.0)` 与旧手写
          `2**attempt * 0.5` 行为对齐（无 jitter；想加可换 `wait_exponential_jitter`）。

        Raises:
            EmbeddingError: 重试 N 次后仍失败（tenacity reraise=True 把最后一次
                异常原样上抛，再被外层包装为 EmbeddingError 保留因果链）。
        """
        retrying = Retrying(
            stop=stop_after_attempt(self.max_retries),
            wait=wait_exponential(multiplier=0.5, max=10.0),
            retry=retry_if_exception_type(_RETRYABLE_EXC),
            reraise=True,
        )
        last_err: Exception | None = None
        try:
            for attempt in retrying:
                with attempt:
                    try:
                        vecs = self._post_embed(texts)
                    except _RETRYABLE_EXC as e:
                        last_err = e
                        log.warning(
                            "嵌入失败（第 %d 次），即将重试：%s",
                            attempt.retry_state.attempt_number,
                            e,
                        )
                        raise
                    # 成功路径：retries = 此前失败的次数 = attempt_number - 1
                    # 外部读这个 metric 想看的是"被重试了几次"，首次成功 = 0。
                    self.stats["retries"] = attempt.retry_state.attempt_number - 1
                    self.stats["requests"] += 1
                    self.stats["embedded"] += len(vecs)
                    return vecs
        except _RETRYABLE_EXC as e:
            # tenacity reraise=True 让最后一次原始异常透传；外层包装为 EmbeddingError
            # 全失败：实际重试了 max_retries - 1 次（首次不算 retry）
            self.stats["retries"] = self.max_retries - 1
            raise EmbeddingError(
                f"重试 {self.max_retries} 次后仍失败：{e}"
            ) from e
        # 不应到达：for 循环要么 return（成功），要么 raise（耗尽）
        raise EmbeddingError(
            f"重试 {self.max_retries} 次后仍失败：{last_err}"
        ) from last_err

    def _post_embed(self, texts: list[str]) -> list[list[float]]:
        """优先用 /api/embed（支持数组=真批量），失败退回逐条 /api/embeddings。"""
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(
            f"{self.host}/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read())
            embs = payload.get("embeddings")
            if embs and len(embs) == len(texts):
                return embs
            raise ValueError(f"/api/embed 返回条数不匹配: {len(embs or [])} != {len(texts)}")
        except urllib.error.HTTPError:
            # 旧版 Ollama 没有 /api/embed → 逐条打老接口
            return [self._post_single(t) for t in texts]

    def _post_single(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{self.host}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())["embedding"]


class HashEmbedder:
    """确定性哈希嵌入 —— **仅供离线测试**，无任何语义能力。

    存在的理由：CI 里没有 Ollama 也要能跑通"摄取 → 写库 → 检索"的**流程**。
    刻意起名 Hash 而不是 Fake/Dummy，并在此明确声明：
    用它跑出来的检索指标**没有参考价值**，不得写进任何评测报告。
    （这正是 P4 `fake_embed` 曾经埋下的坑，这里把话说明白。）
    """

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim
        self.stats = {"requests": 0, "embedded": 0, "cache_hits": 0, "retries": 0}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in t.split():
                h = int(hashlib.blake2b(tok.encode(), digest_size=4).hexdigest(), 16)
                v[h % self.dim] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        self.stats["embedded"] += len(out)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


def get_embedder(kind: str = "auto", *, dim: int = DEFAULT_DIM):
    """选嵌入后端。

    "auto"：Ollama 探测得通就用它，否则退回 HashEmbedder 并**大声告警**。
    """
    if kind == "ollama":
        return OllamaEmbedder()
    if kind == "hash":
        return HashEmbedder(dim=dim)
    if kind != "auto":
        raise ValueError(f"未知嵌入后端: {kind!r}（auto / ollama / hash）")

    emb = OllamaEmbedder()
    try:
        emb.embed_query("probe")
        return emb
    except Exception as e:
        print(
            f"⚠️  Ollama 不可用（{e}）→ 退回 HashEmbedder（无语义能力）。\n"
            "    检索结果仅用于验证流程，**不可用于评测**。"
            "    要真实向量请先 `ollama serve` 并 `ollama pull nomic-embed-text`。"
        )
        return HashEmbedder(dim=dim)


Embedder = Callable[[Sequence[str]], list[list[float]]]

__all__ = [
    "OllamaEmbedder",
    "HashEmbedder",
    "get_embedder",
    "EmbeddingError",
    "DEFAULT_DIM",
    "DEFAULT_MODEL",
]
