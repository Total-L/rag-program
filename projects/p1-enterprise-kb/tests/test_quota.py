"""S2 多租户：配额存储测试。"""

from __future__ import annotations

import pytest
from quota_store import InMemoryQuotaStore
from tenant import Quota, QuotaExceeded, Tenant


@pytest.fixture
def tiny_tenant() -> Tenant:
    """小配额租户便于快速触发上限。"""
    return Tenant(
        tenant_id="tiny",
        name="Tiny",
        quota=Quota(max_queries_per_day=2, max_tokens_per_day=5, max_chunks_indexed=10),
    )


def test_consume_within_limit_ok(tiny_tenant: Tenant) -> None:
    store = InMemoryQuotaStore()
    store.check_and_consume(tiny_tenant, queries=1)
    store.check_and_consume(tiny_tenant, queries=1)
    # 两次都 OK


def test_consume_exceeds_raises(tiny_tenant: Tenant) -> None:
    store = InMemoryQuotaStore()
    store.check_and_consume(tiny_tenant, queries=1)
    store.check_and_consume(tiny_tenant, queries=1)
    with pytest.raises(QuotaExceeded) as exc_info:
        store.check_and_consume(tiny_tenant, queries=1)
    assert exc_info.value.metric == "queries"
    assert exc_info.value.limit == 2
    assert exc_info.value.current == 3
    assert exc_info.value.tenant_id == "tiny"


def test_refund_allows_retry(tiny_tenant: Tenant) -> None:
    store = InMemoryQuotaStore()
    store.check_and_consume(tiny_tenant, queries=1)
    store.check_and_consume(tiny_tenant, queries=1)
    # 满了
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(tiny_tenant, queries=1)
    # 退还一次
    assert store.refund(tiny_tenant, queries=1) is True
    # 再来一次就 OK
    store.check_and_consume(tiny_tenant, queries=1)


def test_refund_wrong_amount_noop(tiny_tenant: Tenant) -> None:
    """refund 的 amount 与最后一条不一致时不退还（避免乱退）；状态保持不变。"""
    store = InMemoryQuotaStore()
    store.check_and_consume(tiny_tenant, queries=1)
    # amount=99 与最后一条 (amount=1) 不匹配 → refund 失败，原事件 push 回
    assert store.refund(tiny_tenant, queries=99) is False
    # 状态不变：剩 1 槽位（tiny quota=2，已用 1） → 还能再 consume 1
    store.check_and_consume(tiny_tenant, queries=1)
    # 再 consume 应该 raise（已用 2/2）
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(tiny_tenant, queries=1)


def test_tenants_independent():
    """两个租户配额互不影响。"""
    a = Tenant(
        tenant_id="aaa",
        name="A",
        quota=Quota(max_queries_per_day=1, max_tokens_per_day=10, max_chunks_indexed=10),
    )
    b = Tenant(
        tenant_id="bbb",
        name="B",
        quota=Quota(max_queries_per_day=5, max_tokens_per_day=10, max_chunks_indexed=10),
    )
    store = InMemoryQuotaStore()
    # a 满
    store.check_and_consume(a, queries=1)
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(a, queries=1)
    # b 没动
    store.check_and_consume(b, queries=3)
    snap_a = store.snapshot(a)
    snap_b = store.snapshot(b)
    assert snap_a["queries"] == 1
    assert snap_b["queries"] == 3


def test_token_metric_separate_from_queries():
    """tokens 与 queries 是独立 metric —— 互不影响。"""
    t = Tenant(
        tenant_id="mix",
        name="Mix",
        quota=Quota(max_queries_per_day=100, max_tokens_per_day=10, max_chunks_indexed=10),
    )
    store = InMemoryQuotaStore()
    store.check_and_consume(t, queries=50)  # queries 用一半
    # tokens 不能超额
    with pytest.raises(QuotaExceeded) as e:
        store.check_and_consume(t, tokens=11)
    assert e.value.metric == "tokens"
    # queries 仍能继续
    store.check_and_consume(t, queries=50)


def test_inject_clock_for_window_boundary():
    """注入 clock 验证 24h 窗口边界（避免真等 24h）。"""
    t = Tenant(
        tenant_id="window",
        name="Window",
        quota=Quota(max_queries_per_day=2, max_tokens_per_day=10, max_chunks_indexed=10),
    )
    fake_now = [1_000_000.0]
    store = InMemoryQuotaStore(clock=lambda: fake_now[0])
    store.check_and_consume(t, queries=2)
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(t, queries=1)
    # 跳到 25 小时后
    fake_now[0] += 25 * 3600
    # 窗口外了，应该又 OK
    store.check_and_consume(t, queries=2)


def test_zero_quota_means_zero_capacity():
    """max=0 的 quota 应该立刻 raise。"""
    t = Tenant(
        tenant_id="zero",
        name="Zero",
        quota=Quota(max_queries_per_day=0, max_tokens_per_day=0, max_chunks_indexed=0),
    )
    store = InMemoryQuotaStore()
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(t, queries=1)
    with pytest.raises(QuotaExceeded):
        store.check_and_consume(t, tokens=1)
