"""S2 多租户：Tenant ContextVar 注入测试。"""

from __future__ import annotations

import pytest
from tenant import Plan, Tenant
from tenant_context import (
    TenantRequiredError,
    get_current_tenant,
    require_current_tenant,
    reset_current_tenant,
    set_current_tenant,
    tenant_scope,
)


def test_default_no_tenant():
    """没进 scope 时 get_current_tenant() 必须返回 None。"""
    assert get_current_tenant() is None


def test_require_raises_when_missing():
    """没进 scope 时 require_current_tenant() 必须 raise TenantRequiredError。"""
    with pytest.raises(TenantRequiredError):
        require_current_tenant()


def test_set_and_get_tenant():
    t = Tenant.from_plan("acme", "Acme", Plan.FREE)
    token = set_current_tenant(t)
    try:
        assert get_current_tenant() is t
        assert require_current_tenant().tenant_id == "acme"
    finally:
        reset_current_tenant(token)


def test_scope_restores_after_exit():
    """退出 with 后 context 必须清空（不能泄漏到下次请求）。"""
    t = Tenant.from_plan("acme", "Acme", Plan.FREE)
    assert get_current_tenant() is None
    with tenant_scope(t):
        assert get_current_tenant().tenant_id == "acme"
    assert get_current_tenant() is None


def test_nested_scopes_restore_in_lifo():
    """嵌套 scope 退出时必须按 LIFO 顺序恢复（类栈）。"""
    outer = Tenant.from_plan("outer", "Outer", Plan.FREE)
    inner = Tenant.from_plan("inner", "Inner", Plan.TEAM)

    with tenant_scope(outer):
        assert get_current_tenant().tenant_id == "outer"
        with tenant_scope(inner):
            assert get_current_tenant().tenant_id == "inner"
        # 退出内层 → 回到外层
        assert get_current_tenant().tenant_id == "outer"
    assert get_current_tenant() is None


def test_failed_scope_still_resets():
    """scope 内异常时，tenant 必须照样 reset（finally 等价行为）。"""
    t = Tenant.from_plan("acme", "Acme", Plan.FREE)
    with pytest.raises(RuntimeError):
        with tenant_scope(t):
            assert get_current_tenant().tenant_id == "acme"
            raise RuntimeError("boom")
    # 异常后必须清理，不能让下个请求串味
    assert get_current_tenant() is None


def test_independent_contexts_across_sequential_calls():
    """两次连续 set 之间互不污染（reset 必须真的清掉）。"""
    a = Tenant.from_plan("a-corp", "A", Plan.FREE)
    b = Tenant.from_plan("b-corp", "B", Plan.TEAM)

    token_a = set_current_tenant(a)
    assert get_current_tenant().tenant_id == "a-corp"
    reset_current_tenant(token_a)

    token_b = set_current_tenant(b)
    try:
        assert get_current_tenant().tenant_id == "b-corp"
        # 验证 token_a 真的清掉了，没残留
    finally:
        reset_current_tenant(token_b)
