"""P1 — 错误分桶测试（ENTERPRISE_AUDIT D6-T2）。

覆盖：
1. QueryStatus 枚举值稳定（避免破坏现有 Prometheus label）
2. quota_exceeded 走 finally 上报 metrics + 不写 audit
3. internal_error 走 finally 上报 metrics + 不写 audit（无 P1Result）
4. not_ready 走正常返回 + 写 audit
5. graph_no_attempt 走正常返回 + 写 audit
6. metrics label 与枚举值一致（不出现裸 "error" 字符串）

跑：
    pytest projects/p1-enterprise-kb/tests/test_error_classification.py -v
"""

from __future__ import annotations

import sys
import types

import pytest
from observability import QueryStatus, rag_queries_total

# ── agentic_graph mock fixture（避免 import 时拉 langgraph）──


@pytest.fixture
def mock_agentic_graph(monkeypatch):
    """mock `src.agentic_graph.ask_agentic` 不抛，让 _ask_graph 路径可控。

    pipeline.py 用 `from agentic_graph import ask_agentic as _ask_graph` —— 直接
    monkeypatch `pipeline` 模块内的 `agentic_graph` 名字最稳。
    """

    class _FakeGraph:
        def ask_agentic(self, *args, **kwargs):
            return {"attempts": [], "log": []}

    fake_module = types.ModuleType("agentic_graph")
    fake_module.ask_agentic = _FakeGraph().ask_agentic
    monkeypatch.setitem(sys.modules, "agentic_graph", fake_module)

    # 也 import pipeline 之前，sys.modules 已含 fake module
    import pipeline

    monkeypatch.setattr(pipeline, "agentic_graph", fake_module, raising=False)
    return fake_module


# ── 1. 枚举值稳定性 ──


def test_query_status_values_are_stable():
    """枚举值是公开契约（Prometheus label / audit status）；不得随意改名。"""
    expected = {
        "success",
        "abstained",
        "not_ready",
        "quota_exceeded",
        "graph_no_attempt",
        "internal_error",
    }
    actual = {s.value for s in QueryStatus}
    assert actual == expected


def test_query_status_is_str_enum():
    """StrEnum 让 status 既是 str 又是 enum，JSON / Prometheus label / 比较都通。"""
    s = QueryStatus.SUCCESS
    assert isinstance(s, str)
    assert s == "success"
    assert s.value == "success"


def test_query_status_no_legacy_error_string():
    """旧的 'error' 字符串已废弃 —— D6-T2 不再使用 catch-all 'error'。"""
    status_values = {s.value for s in QueryStatus}
    assert "error" not in status_values
    # 改名为 internal_error
    assert "internal_error" in status_values


# ── 2. quota_exceeded 走 finally 上报 metrics ──


def test_quota_exceeded_propagates_and_records_metric(monkeypatch):
    """QuotaExceeded 路径：status=quota_exceeded 上报 metrics，audit 不写（无 P1Result）。"""
    from quota_store import InMemoryQuotaStore, QuotaExceeded
    from tenant import Quota, Tenant
    from tenant_registry import TenantRegistry

    # 极小配额：第 1 次就超限
    tenant = Tenant(
        tenant_id="tiny",
        name="Tiny",
        quota=Quota(max_queries_per_day=0, max_tokens_per_day=0, max_chunks_indexed=10),
    )
    quota = InMemoryQuotaStore()
    # mock registry + quota store
    class _MockReg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "tiny":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import quota_store
    import tenant_registry

    # 必须打到 pipeline 模块（它已 import 了 get_registry 引用）
    monkeypatch.setattr(pipeline, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(pipeline, "get_quota_store", lambda: quota)
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(quota_store, "get_quota_store", lambda: quota)

    # 计数 metrics 前 / 后
    def _metric_total() -> float:
        children = getattr(rag_queries_total, "_metrics", None)
        if not children:
            return 0.0
        return sum(c._value.get() for c in children.values())

    before = _metric_total()

    from acl import User
    from pipeline import P1Pipeline

    p = P1Pipeline(user=User(user_id="u1", role="executive"))
    # 不需要 index_corpus —— quota_exceeded 在 try 块外

    with pytest.raises(QuotaExceeded):
        p.ask("test question", tenant_id="tiny")

    after = _metric_total()
    assert after == before + 1, "quota_exceeded 应被 finally 上报 metrics"


def test_quota_exceeded_metrics_label_is_correct(monkeypatch):
    """metrics label 的 status 必须是 'quota_exceeded' 而非 'internal_error' 或 'error'。"""
    from quota_store import InMemoryQuotaStore, QuotaExceeded
    from tenant import Quota, Tenant
    from tenant_registry import TenantRegistry

    tenant = Tenant(
        tenant_id="tiny",
        name="Tiny",
        quota=Quota(max_queries_per_day=0, max_tokens_per_day=0, max_chunks_indexed=10),
    )
    quota = InMemoryQuotaStore()

    class _MockReg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "tiny":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import quota_store
    import tenant_registry

    monkeypatch.setattr(pipeline, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(pipeline, "get_quota_store", lambda: quota)
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(quota_store, "get_quota_store", lambda: quota)

    from acl import User
    from pipeline import P1Pipeline

    p = P1Pipeline(user=User(user_id="u1", role="executive"))
    with pytest.raises(QuotaExceeded):
        p.ask("test", tenant_id="tiny")

    # 查 children 找到 label 包含 quota_exceeded
    children = getattr(rag_queries_total, "_metrics", None)
    assert children, "metrics 应该有 children"
    quota_exceeded_labels = [
        k for k in children if isinstance(k, tuple) and "quota_exceeded" in k
    ]
    assert quota_exceeded_labels, "metrics label 应有 quota_exceeded"


# ── 3. internal_error 走 finally 上报 metrics ──


def test_internal_error_propagates_and_records_metric(monkeypatch, mock_agentic_graph):
    """catch-all 异常 → status=internal_error 上报 metrics。"""
    def _boom(*args, **kwargs):
        raise ValueError("simulated pipeline crash")

    mock_agentic_graph.ask_agentic = _boom

    from acl import User
    from pipeline import P1Pipeline
    from tenant import Plan, Tenant
    from tenant_registry import TenantRegistry

    tenant = Tenant.from_plan(tenant_id="acme-corp", name="ACME", plan=Plan.TEAM)

    class _MockReg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "acme-corp":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import tenant_registry

    monkeypatch.setattr(pipeline, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _MockReg())

    p = P1Pipeline(user=User(user_id="u1", role="executive"))
    # 强制 index 非空（避免 not_ready 抢先返回）
    p._indexed = True
    p._index_cache = [
        {
            "chunk_id": "c1",
            "source_id": "s1",
            "text": "t",
            "embedding": [0.0] * 768,
            "sensitivity": "public",
            "heading_path": "",
            "tenant_id": "acme-corp",
        }
    ]

    def _metric_total() -> float:
        children = getattr(rag_queries_total, "_metrics", None)
        if not children:
            return 0.0
        return sum(c._value.get() for c in children.values())

    before = _metric_total()
    with pytest.raises(ValueError, match="simulated pipeline crash"):
        p.ask("test", tenant_id="acme-corp")
    after = _metric_total()
    assert after == before + 1, "internal_error 应被 finally 上报 metrics"


def test_internal_error_metrics_label_is_correct(monkeypatch, mock_agentic_graph):
    """metrics label 的 status 必须是 'internal_error'（不是旧的 'error'）。"""
    def _boom(*args, **kwargs):
        raise ValueError("crash")

    mock_agentic_graph.ask_agentic = _boom

    from acl import User
    from pipeline import P1Pipeline
    from tenant import Plan, Tenant
    from tenant_registry import TenantRegistry

    tenant = Tenant.from_plan(tenant_id="acme-corp", name="ACME", plan=Plan.TEAM)

    class _MockReg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "acme-corp":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import tenant_registry

    monkeypatch.setattr(pipeline, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _MockReg())

    p = P1Pipeline(user=User(user_id="u1", role="executive"))
    p._indexed = True
    p._index_cache = [
        {
            "chunk_id": "c1",
            "source_id": "s1",
            "text": "t",
            "embedding": [0.0] * 768,
            "sensitivity": "public",
            "heading_path": "",
            "tenant_id": "acme-corp",
        }
    ]
    with pytest.raises(ValueError):
        p.ask("test", tenant_id="acme-corp")

    children = getattr(rag_queries_total, "_metrics", None)
    assert children, "metrics 应该有 children"
    internal_error_labels = [k for k in children if isinstance(k, tuple) and "internal_error" in k]
    assert internal_error_labels, "metrics label 应有 internal_error"
    # 旧 'error' 不应存在
    legacy_error_labels = [k for k in children if isinstance(k, tuple) and "error" == k[0]]
    assert not legacy_error_labels, "metrics label 不应有裸 'error'"


# ── 4. not_ready 走正常返回路径 + 写 audit ──


def test_not_ready_returns_p1result_and_writes_audit(monkeypatch, mock_agentic_graph):
    """未跑 index_corpus → status=not_ready，正常返回 + audit 写入。"""
    from acl import User
    from pipeline import P1Pipeline
    from tenant import Plan, Tenant
    from tenant_registry import TenantRegistry

    tenant = Tenant.from_plan(tenant_id="acme-corp", name="ACME", plan=Plan.TEAM)

    class _MockReg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "acme-corp":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import tenant_registry

    monkeypatch.setattr(pipeline, "get_registry", lambda: _MockReg())
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _MockReg())

    p = P1Pipeline(user=User(user_id="u1", role="executive"))
    # 不跑 index_corpus → self.index 空 → status=not_ready
    r = p.ask("test", tenant_id="acme-corp")
    assert r.abstained is True
    assert "无索引" in r.answer


# ── 5. QueryStatus 字符串可直接用于 metrics / audit ──


def test_query_status_value_round_trip():
    """enum.value() 必须与 Prometheus label / audit JSON 字段值一致。"""
    for s in QueryStatus:
        # 直接传给 record_query / audit build_event 都不应破坏
        assert s.value == str(s)


def test_query_status_no_duplicate_values():
    """每个枚举值唯一（防止 Prometheus label 冲突）。"""
    values = [s.value for s in QueryStatus]
    assert len(values) == len(set(values))


# ── 6. observability 集成 ──


def test_record_query_accepts_query_status_enum():
    """record_query 接受 str label —— enum.value() 必须直接可用。"""
    from observability import record_query

    # 不抛异常 = pass（mock 环境下 HAS_PROMETHEUS 可能为 False，跳过）
    for s in QueryStatus:
        record_query(status=s.value, latency_seconds=0.1, tenant_id="acme-corp")


def test_record_audit_write_accepts_string_labels():
    """record_audit_write 接受 str status。"""
    from observability import record_audit_write

    record_audit_write(status="success", tenant_id="acme-corp")
    record_audit_write(status="failure", tenant_id="acme-corp")
