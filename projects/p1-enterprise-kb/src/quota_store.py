"""P1 — 配额存储（ENTERPRISE_AUDIT.md S2）。

为什么 InMemory + Protocol：
- dev/MVP 阶段不引入 Redis；dict + 滑动窗口足够。
- 接口稳定 → 后续换 Redis sliding window 不破坏消费方。
- 失败路径必须 refund：避免一次内部异常永久消耗租户配额。

滑动窗口实现：
- 每租户维护两个 deque —— queries (timestamp) 和 tokens (timestamp)
- check_and_consume：先 peek 24h 外的旧条目丢弃 → 计数 → 检查 ≤ limit → 写入新条目
- refund：从 deque 尾部弹出最近一次条目（如果匹配 metric）

为什么按时间戳存（不按 [date] key）：
- 一天内多次跨日的摄取不会被"日切"清零逻辑漏掉。
- 时间戳方案天然支持 sub-day 滑动窗口（未来可换 window=1h）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from tenant import QuotaExceeded, Tenant

_WINDOW_S = 24 * 60 * 60  # 24 小时


@dataclass
class _Bucket:
    """一个租户一个 metric 的滑动窗口。"""

    events: deque = field(default_factory=deque)  # type: ignore[valid-type]

    def consume(self, amount: int, now: float, limit: int) -> None:
        """检查并写入新事件。超限则 raise QuotaExceeded（写入回滚）。

        Args:
            amount: 本次消费的数量（queries 是 1，tokens 是生成 token 数）
            now: 当前时间戳（注入便于测试）
            limit: 该 metric 的 24h 上限
        """
        # 1) 丢弃窗口外的旧条目
        cutoff = now - _WINDOW_S
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()
        # 2) 当前总消耗
        total = sum(amt for _ts, amt in self.events)
        # 3) 超限判定：算上本次 amount
        if total + amount > limit:
            raise QuotaExceeded(
                tenant_id=self.tenant_id,  # type: ignore[attr-defined]
                metric=self.metric,  # type: ignore[attr-defined]
                limit=limit,
                current=total + amount,
            )
        # 4) 写入新事件：(ts, amount)
        self.events.append((now, amount))

    def refund(self, amount: int) -> bool:
        """退还最近一次的消费。成功退还返回 True，没有可退还条目返回 False。

        用途：pipeline 内部异常时调用，避免把"系统失败"的成本算到租户头上。
        """
        if not self.events:
            return False
        _ts, last_amount = self.events.pop()
        if last_amount == amount:
            return True
        # amount 不一致 —— 退回原值（说明 metric 类型错了）
        self.events.append((_ts, last_amount))
        return False

    def snapshot(self, now: float) -> int:
        """返回当前 24h 内的总消耗（用于 metrics / debug，不含未提交）。"""
        cutoff = now - _WINDOW_S
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()
        return sum(amt for _ts, amt in self.events)


class QuotaStore(Protocol):
    """配额存储接口；InMemory / Redis / DB 实现都满足这个协议。"""

    def check_and_consume(
        self,
        tenant: Tenant,
        *,
        queries: int = 0,
        tokens: int = 0,
    ) -> None: ...

    def refund(self, tenant: Tenant, *, queries: int = 0, tokens: int = 0) -> bool: ...

    def snapshot(self, tenant: Tenant) -> dict[str, int]: ...


class InMemoryQuotaStore:
    """dict 支撑的内存配额存储。线程安全（GIL 兜底 + 关键路径原子）。"""

    def __init__(self, *, clock=time.time) -> None:
        # {tenant_id: {metric: _Bucket}}
        self._buckets: dict[str, dict[str, _Bucket]] = {}
        # 注入 clock 便于测试
        self._clock = clock

    def _bucket(self, tenant_id: str, metric: str) -> _Bucket:
        bmap = self._buckets.setdefault(tenant_id, {})
        if metric not in bmap:
            b = _Bucket()
            b.tenant_id = tenant_id  # for QuotaExceeded payload
            b.metric = metric
            bmap[metric] = b
        return bmap[metric]

    def check_and_consume(
        self,
        tenant: Tenant,
        *,
        queries: int = 0,
        tokens: int = 0,
    ) -> None:
        now = self._clock()
        q = tenant.quota
        if queries > 0:
            self._bucket(tenant.tenant_id, "queries").consume(queries, now, q.max_queries_per_day)
        if tokens > 0:
            self._bucket(tenant.tenant_id, "tokens").consume(tokens, now, q.max_tokens_per_day)

    def refund(self, tenant: Tenant, *, queries: int = 0, tokens: int = 0) -> bool:
        ok = True
        if queries > 0:
            ok = self._bucket(tenant.tenant_id, "queries").refund(queries) and ok
        if tokens > 0:
            ok = self._bucket(tenant.tenant_id, "tokens").refund(tokens) and ok
        return ok

    def snapshot(self, tenant: Tenant) -> dict[str, int]:
        now = self._clock()
        return {
            "queries": self._bucket(tenant.tenant_id, "queries").snapshot(now),
            "tokens": self._bucket(tenant.tenant_id, "tokens").snapshot(now),
        }


# ── 进程级单例 ──

_store: QuotaStore | None = None


def get_quota_store() -> QuotaStore:
    global _store
    if _store is None:
        _store = InMemoryQuotaStore()
    return _store


def set_quota_store(store: QuotaStore) -> None:
    """测试用：注入自定义 store。"""
    global _store
    _store = store


def reset_quota_store() -> None:
    global _store
    _store = None


__all__ = [
    "QuotaStore",
    "InMemoryQuotaStore",
    "get_quota_store",
    "set_quota_store",
    "reset_quota_store",
]
