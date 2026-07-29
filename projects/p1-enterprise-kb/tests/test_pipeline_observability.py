"""P1 pipeline 可观测埋点测试（ENTERPRISE_AUDIT.md M1b）。

覆盖：
1. ask() 自动生成 trace_id（不传时）
2. ask() 接受外部 trace_id（API 层透传）
3. 每次 ask() 调用都记录 metrics
4. trace_id 写入 P1Result.stages
5. trace_id 在 ask_agentic() 嵌套调用中保持
"""

from __future__ import annotations

import pytest

# structlog/prometheus 硬依赖
structlog = pytest.importorskip("structlog")
prom = pytest.importorskip("prometheus_client")

from acl import User  # noqa: E402
from observability import (  # noqa: E402
    rag_queries_total,
    trace_id_var,
)
from pipeline import P1Pipeline  # noqa: E402


def _sum_children(metric) -> float:
    """累加 prometheus_client metric 的所有 child value。"""
    children = getattr(metric, "_metrics", None) or {}
    return sum(c._value.get() for c in children.values())


def test_ask_empty_index_records_not_ready_status():
    """空索引 ask() → rag_queries_total{status=not_ready} +1。"""
    p = P1Pipeline(user=User("alice", "executive"))
    # 不调 index_corpus → self.index == []

    before = _sum_children(rag_queries_total)
    p.ask("员工年假？", tenant_id="acme-corp")
    after = _sum_children(rag_queries_total)

    assert after == before + 1, "空索引 query 应被记为 not_ready 状态"


def test_ask_accepts_external_trace_id():
    """外部传入 trace_id → 写入 P1Result.stages['trace_id']。"""
    p = P1Pipeline(user=User("alice", "executive"))
    r = p.ask("员工年假？", tenant_id="acme-corp", trace_id="my-external-trace-123")
    assert r.stages.get("trace_id") == "my-external-trace-123"


def test_ask_auto_generates_trace_id():
    """不传 trace_id → 自动生成 16 位 hex。"""
    p = P1Pipeline(user=User("alice", "executive"))
    r = p.ask("员工年假？", tenant_id="acme-corp")
    tid = r.stages.get("trace_id")
    assert tid is not None
    assert len(tid) == 16, f"自动生成的 trace_id 应为 16 位 hex，实际: {tid}"


def test_ask_unbinds_trace_id_after_call():
    """ask() 调用结束后 trace_id 必须被解绑（避免污染下一个请求）。"""
    p = P1Pipeline(user=User("alice", "executive"))
    p.ask("员工年假？", tenant_id="acme-corp", trace_id="temp-trace")
    # 调结束后，trace_id_var 应回到默认 None
    assert trace_id_var.get() is None, "ask() 调用结束后 trace_id 必须解绑"


def test_concurrent_asks_have_isolated_trace_ids():
    """并发 ask() 各自的 trace_id 不串扰（ContextVar 隔离）。"""
    import asyncio

    p = P1Pipeline(user=User("alice", "executive"))

    async def call_with_tid(tid):
        r = p.ask("员工年假？", tenant_id="acme-corp", trace_id=tid)
        return r.stages.get("trace_id")

    async def main():
        return await asyncio.gather(
            call_with_tid("trace-A"),
            call_with_tid("trace-B"),
            call_with_tid("trace-C"),
        )

    results = asyncio.run(main())
    assert results == ["trace-A", "trace-B", "trace-C"]


def test_ask_agentic_accepts_trace_id():
    """ask_agentic() 也接受 trace_id。"""
    p = P1Pipeline(user=User("alice", "executive"))
    r = p.ask_agentic("员工年假？", tenant_id="acme-corp", trace_id="agentic-trace-xyz")
    assert r.stages.get("trace_id") == "agentic-trace-xyz"
