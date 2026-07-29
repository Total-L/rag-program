"""P1 可观测基线测试（ENTERPRISE_AUDIT.md M1）。

覆盖：
1. trace_id 上下文变量（set/get/new）
2. structlog JSON 输出格式
3. Prometheus metrics 上报函数（record_query/record_abstention/record_citation_valid/record_retrieval_hit）
4. /healthz / /readyz 端点响应
5. metrics_response 生成 Prometheus 文本格式
"""

from __future__ import annotations

import io
import json
import sys

import pytest


def _sum_children(metric) -> float:
    """累加 metric 所有 labelled children 的值（兼容 prometheus_client 1.x）。"""
    children = getattr(metric, "_metrics", None) or {}
    return sum(c._value.get() for c in children.values())


# structlog/prometheus 都是硬依赖；未装时整文件 skip
structlog = pytest.importorskip("structlog")
prom = pytest.importorskip("prometheus_client")

from observability import (  # noqa: E402
    bind_trace_id,
    configure_logging,
    current_trace_id,
    get_logger,
    healthz_response,
    metrics_response,
    new_trace_id,
    readyz_response,
    record_abstention,
    record_citation_valid,
    record_query,
    record_retrieval_hit,
    trace_id_var,
)

# ---- trace_id ----


def test_new_trace_id_is_unique():
    a = new_trace_id()
    b = new_trace_id()
    assert a != b
    assert len(a) == 16  # 8 bytes hex


def test_bind_and_current_trace_id():
    assert current_trace_id() is None
    token = bind_trace_id("test-trace-123")
    try:
        assert current_trace_id() == "test-trace-123"
    finally:
        trace_id_var.reset(token)
    assert current_trace_id() is None


def test_trace_id_isolated_between_contexts():
    """trace_id 用 ContextVar，不同请求互不干扰。"""
    import asyncio

    async def task(name, tid):
        token = bind_trace_id(tid)
        await asyncio.sleep(0.01)
        result = current_trace_id()
        trace_id_var.reset(token)
        return result

    async def main():
        return await asyncio.gather(
            task("A", "trace-A"),
            task("B", "trace-B"),
        )

    results = asyncio.run(main())
    assert results == ["trace-A", "trace-B"]


# ---- structlog ----


def test_configure_logging_emits_json():
    """configure_logging 后，log 输出是合法 JSON。"""
    configure_logging(level="INFO")
    log = get_logger("test")

    # 捕获 stdout
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        log.info("test_event", key="value", n=42)
    finally:
        sys.stdout = old_stdout

    output = captured.getvalue().strip()
    assert output, "structlog 应输出日志"
    parsed = json.loads(output)
    assert parsed["event"] == "test_event"
    assert parsed["key"] == "value"
    assert parsed["n"] == 42
    assert "timestamp" in parsed
    assert parsed["level"] == "info"


def test_logger_includes_trace_id_when_bound():
    """绑定 trace_id 后，日志应自动包含 trace_id 字段。

    用 structlog.bind_contextvars()（不是 raw ContextVar），
    否则 merge_contextvars processor 看不到。
    """
    import structlog

    configure_logging(level="INFO")
    log = get_logger("test")
    structlog.contextvars.bind_contextvars(trace_id="tr-abc123")
    try:
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            log.info("with_trace")
        finally:
            sys.stdout = old_stdout
        parsed = json.loads(captured.getvalue().strip())
        assert parsed.get("trace_id") == "tr-abc123", f"trace_id 未出现在日志: {parsed}"
    finally:
        structlog.contextvars.unbind_contextvars("trace_id")


# ---- metrics ----


def test_record_query_increments_counter_and_observes_histogram():
    """record_query 应同时增加 Counter 和 observe Histogram。"""
    from observability import rag_queries_total, rag_query_latency_seconds

    # S2：metrics 加了 tenant_id label —— 必须传两个
    def _total(metric) -> float:
        children = getattr(metric, "_metrics", None) or {}
        return sum(c._value.get() for c in children.values())

    before_total = _total(rag_queries_total)

    # Histogram count 用 collect() 拿（标准 API，避免依赖 _count 内部属性）
    def _count():
        samples = list(
            rag_query_latency_seconds.labels(status="test", tenant_id="acme-corp").collect()
        )[0].samples
        return next(s.value for s in samples if s.name.endswith("_count"))

    before_count = _count()

    record_query(status="test", latency_seconds=0.5, tenant_id="acme-corp")

    after_total = _total(rag_queries_total)
    after_count = _count()

    assert after_total == before_total + 1, "queries_total 必须 +1"
    assert after_count == before_count + 1, "histogram count 必须 +1"


def test_record_abstention():
    from observability import rag_abstention_total

    before = _sum_children(rag_abstention_total)
    record_abstention()
    after = _sum_children(rag_abstention_total)
    assert after == before + 1


def test_record_citation_valid():
    from observability import rag_citation_valid_total

    before = _sum_children(rag_citation_valid_total)
    record_citation_valid()
    after = _sum_children(rag_citation_valid_total)
    assert after == before + 1


def test_record_retrieval_hit():
    from observability import rag_retrieval_hits_total

    before = _sum_children(rag_retrieval_hits_total)
    record_retrieval_hit(tenant_id="acme-corp")
    after = _sum_children(rag_retrieval_hits_total)
    assert after == before + 1


def test_metrics_response_returns_prometheus_format():
    """metrics_response 生成合法 Prometheus 文本。"""
    body, status, headers = metrics_response()
    assert status == 200
    assert "text/plain" in headers["Content-Type"]
    text = body.decode() if isinstance(body, bytes) else body
    # Prometheus 文本必含 # HELP 和 # TYPE
    assert "# HELP" in text
    assert "# TYPE" in text
    # 我们的指标必须在
    assert "rag_query_latency_seconds" in text or "rag_queries_total" in text


# ---- health / ready ----


def test_healthz_returns_ok():
    body, status = healthz_response()
    assert status == 200
    assert body == {"status": "ok"}


def test_readyz_returns_503_when_not_indexed():
    body, status = readyz_response(pipeline_indexed=False)
    assert status == 503
    assert body["status"] == "not_ready"
    assert "index_corpus" in body["reason"]


def test_readyz_returns_200_when_indexed():
    body, status = readyz_response(pipeline_indexed=True)
    assert status == 200
    assert body["status"] == "ready"
