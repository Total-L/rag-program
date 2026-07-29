"""P1 可观测基线（ENTERPRISE_AUDIT.md M1）。

提供：
1. structlog JSON 日志（输出到 stdout，可被采集器解析）
2. trace_id 上下文变量（贯穿 ask → agentic_graph → LLM 调用）
3. Prometheus metrics：
   - rag_query_latency_seconds (Histogram)
   - rag_queries_total (Counter)
   - rag_abstention_total (Counter)
   - rag_citation_valid_total (Counter)
   - rag_retrieval_hits_total (Counter, 用于算 hit_rate)
4. /healthz, /readyz, /metrics 端点（FastAPI app）

设计取舍（per CLAUDE.md "Simplicity First"）：
- 不引入 OTel collector（部署时再考虑）；只暴露 Prometheus 端点
- trace_id 不透传到 LLM 调用层（Ollama 是 black box，加 trace 收益小）
- metrics 用 prometheus_client（同步，与 FastAPI 兼容）
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from enum import StrEnum

import structlog

# ── 0. query 状态分桶（ENTERPRISE_AUDIT D6-T2）──


class QueryStatus(StrEnum):
    """query 终态分桶。事故定位、SLO 计算、告警触发都基于这个枚举。

    设计取舍：
    - StrEnum 而非 IntEnum：Prometheus label 必须是字符串；JSON 序列化直接用值。
    - 不与 HTTP status code 强绑定（200 也可能 status=internal_error）；
      status 是**业务终态**，HTTP status 是**传输层**。
    - 加新枚举值时**只追加**，不改旧值字符串；rag_queries_total 已按此 label
      累积，破坏 label 值会让历史 metric 失去意义。

    业务终态：
    - SUCCESS / ABSTAINED / NOT_READY：正常路径
    - QUOTA_EXCEEDED：租户配额耗尽（429 语义）
    - GRAPH_NO_ATTEMPT：LangGraph 返空 attempts（极少见，重试可恢复）
    - INTERNAL_ERROR：catch-all 异常（500 语义；具体子因看 trace 日志）
    """

    SUCCESS = "success"
    ABSTAINED = "abstained"
    NOT_READY = "not_ready"
    QUOTA_EXCEEDED = "quota_exceeded"
    GRAPH_NO_ATTEMPT = "graph_no_attempt"
    INTERNAL_ERROR = "internal_error"


# ── 1. trace_id 上下文变量 ──
# 用于在嵌套调用（pipeline.ask → ask_agentic → _ask_legacy → LLM）中
# 关联日志和 metrics。
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """生成新 trace_id（16 位 hex）。"""
    import secrets

    return secrets.token_hex(8)


def current_trace_id() -> str | None:
    return trace_id_var.get()


def bind_trace_id(trace_id: str):
    """绑定 trace_id 到当前 context（用于 ask(question, trace_id=...)）。

    同时设置：
    - ContextVar（programmatic 检查用）
    - structlog contextvars（日志自动包含 trace_id 字段）

    Returns:
        ContextVar token（用于 reset）
    """
    try:
        import structlog

        structlog.contextvars.bind_contextvars(trace_id=trace_id)
    except ImportError:
        pass
    return trace_id_var.set(trace_id)


def unbind_trace_id(token) -> None:
    """解除 trace_id 绑定（与 bind_trace_id 配对）。"""
    try:
        import structlog

        structlog.contextvars.unbind_contextvars("trace_id")
    except (ImportError, KeyError):
        pass
    trace_id_var.reset(token)


# ── 2. structlog 配置 ──


def configure_logging(level: str = "INFO") -> None:
    """配置 structlog 输出 JSON 日志到 stdout。

    字段：timestamp, level, event, trace_id（若有）, **kwargs
    部署时可直接被 Fluent Bit / Vector / Loki 采集。
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "rag"):
    """获取配置好的 structlog logger。"""
    return structlog.get_logger(name)


# ── 3. Prometheus metrics ──

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


# ── 3. Prometheus metrics ──

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


def _create_metric(name: str, ctor, *args, **kwargs):
    """创建 metric；已存在则复用 / 冲突则 unregister 重试。

    为什么不用 module-level 裸创建：prometheus_client 模块级 Histogram/Counter
    在重复 import 时（test fixture reload 模块会触发）会抛 'Duplicated timeseries'
    或 'Incorrect label names'。这里把创建逻辑封装，重复调用安全。
    """
    for collector in list(REGISTRY._collector_to_names.keys()):  # type: ignore[attr-defined]
        names = REGISTRY._collector_to_names.get(collector, set())  # type: ignore[attr-defined]
        if any(n.startswith(name) for n in names):
            try:
                REGISTRY.unregister(collector)
            except Exception:
                pass
            break
    return ctor(name, *args, **kwargs)


# 在 module 加载时建一次（生产路径）
if HAS_PROMETHEUS:
    rag_query_latency_seconds = _create_metric(
        "rag_query_latency_seconds",
        Histogram,
        "End-to-end query latency (seconds)",
        labelnames=["status", "tenant_id"],
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    )
    rag_queries_total = _create_metric(
        "rag_queries_total",
        Counter,
        "Total queries processed",
        labelnames=["status", "tenant_id"],
    )
    rag_abstention_total = _create_metric(
        "rag_abstention_total",
        Counter,
        "Queries where the pipeline abstained",
        labelnames=["tenant_id"],
    )
    rag_citation_valid_total = _create_metric(
        "rag_citation_valid_total",
        Counter,
        "Queries with non-empty citations (or correctly abstained)",
        labelnames=["tenant_id"],
    )
    rag_retrieval_hits_total = _create_metric(
        "rag_retrieval_hits_total",
        Counter,
        "Queries where retrieved_source_ids intersect expected_doc_ids (per-row)",
        labelnames=["tenant_id"],
    )
    rag_indexed_chunks = _create_metric(
        "rag_indexed_chunks",
        Gauge,
        "Number of chunks currently in the index",
        labelnames=["tenant_id"],
    )
    rag_audit_write_total = _create_metric(
        "rag_audit_write_total",
        Counter,
        "Total audit log writes (status=success|failure; failure = disk/permission/etc)",
        labelnames=["status", "tenant_id"],
    )
    # ★ S4-T5：prompt 防御指标 (action=block|warn|pass; kind=InputTooLongError|FQN 类名)
    rag_prompt_guard_total = _create_metric(
        "rag_prompt_guard_total",
        Counter,
        "Prompt guard events (action=block|warn|pass; kind=GuardError type)",
        labelnames=["action", "kind"],
    )
    # ★ S4-T5：输出 schema 校验（reason=ok|parse_error|schema_mismatch|injection_marker_in_output|citations_invalid）
    rag_output_validation_total = _create_metric(
        "rag_output_validation_total",
        Counter,
        "LLM output validation results (reason=ok|parse_error|schema_mismatch|injection_marker_in_output|citations_invalid)",
        labelnames=["reason"],
    )
    # ★ S4-T5：检索到的 evidence 中检出注入痕迹（细粒度安全信号）
    rag_security_flag_total = _create_metric(
        "rag_security_flag_total",
        Counter,
        "Security flag events (kind=pii_seen|injection_in_input|injection_in_output)",
        labelnames=["kind"],
    )


def metrics_response():
    """生成 Prometheus metrics 响应。"""
    if not HAS_PROMETHEUS:
        return ("prometheus_client not installed", 500, {"Content-Type": "text/plain"})
    payload = generate_latest()
    return (payload, 200, {"Content-Type": CONTENT_TYPE_LATEST})


# ── 4. 指标上报便捷函数 ──


def record_query(
    status: str,
    latency_seconds: float,
    *,
    tenant_id: str = "unknown",
) -> None:
    """记录一次 query 的 latency 和状态（按 tenant 分桶）。"""
    if not HAS_PROMETHEUS:
        return
    rag_query_latency_seconds.labels(status=status, tenant_id=tenant_id).observe(latency_seconds)  # type: ignore[union-attr]
    rag_queries_total.labels(status=status, tenant_id=tenant_id).inc()  # type: ignore[union-attr]


def record_abstention(*, tenant_id: str = "unknown") -> None:
    if HAS_PROMETHEUS:
        rag_abstention_total.labels(tenant_id=tenant_id).inc()  # type: ignore[union-attr]


def record_citation_valid(*, tenant_id: str = "unknown") -> None:
    if HAS_PROMETHEUS:
        rag_citation_valid_total.labels(tenant_id=tenant_id).inc()  # type: ignore[union-attr]


def record_retrieval_hit(*, tenant_id: str = "unknown") -> None:
    if HAS_PROMETHEUS:
        rag_retrieval_hits_total.labels(tenant_id=tenant_id).inc()  # type: ignore[union-attr]


def record_audit_write(*, status: str, tenant_id: str = "unknown") -> None:
    """记录一次 audit 写入（status="success" 或 "failure"）。

    写入失败也上报：S3 失败信号必须显式，运维能基于此设告警；
    audit 写入失败不应阻断 query（设计取舍详见 audit.py 顶部注释）。
    """
    if HAS_PROMETHEUS:
        rag_audit_write_total.labels(status=status, tenant_id=tenant_id).inc()  # type: ignore[union-attr]


# ★ S4-T5：prompt 防御埋点
def record_prompt_guard(*, action: str, kind: str) -> None:
    """记录一次 prompt 防御事件。

    Args:
        action: block|warn|pass
        kind: GuardError 类名 / "guard_error" 等自由文本
    """
    if HAS_PROMETHEUS:
        rag_prompt_guard_total.labels(action=action, kind=kind).inc()  # type: ignore[union-attr]


# ★ S4-T5：输出 schema 校验埋点
def record_output_validation(*, reason: str) -> None:
    """记录一次 LLM 输出 schema 校验结果。

    Args:
        reason: ok | parse_error | schema_mismatch | injection_marker_in_output | citations_invalid
    """
    if HAS_PROMETHEUS:
        rag_output_validation_total.labels(reason=reason).inc()  # type: ignore[union-attr]


# ★ S4-T5：安全标志埋点
def record_security_flag(*, kind: str) -> None:
    """记录一次安全标志事件。

    Args:
        kind: pii_seen | injection_in_input | injection_in_output
    """
    if HAS_PROMETHEUS:
        rag_security_flag_total.labels(kind=kind).inc()  # type: ignore[union-attr]


# ── 5. /healthz / /readyz ──


def healthz_response():
    """Liveness probe — 进程是否在跑。

    注意：liveness 不检查依赖（Ollama / DB），那是 readiness 的事。
    """
    return {"status": "ok"}, 200


def readyz_response(pipeline_indexed: bool):
    """Readiness probe — 能否服务请求。

    Args:
        pipeline_indexed: P1Pipeline._indexed（是否跑过 index_corpus）
    """
    if not pipeline_indexed:
        return {"status": "not_ready", "reason": "index_corpus not called"}, 503
    return {"status": "ready", "indexed_chunks": _indexed_chunks()}, 200


def _indexed_chunks() -> int:
    """取当前索引 chunk 数（用 Gauge 间接）。"""
    # 实现简单：返回 Gauge 值；外部可设置 rag_indexed_chunks.set(N)
    if HAS_PROMETHEUS and hasattr(rag_indexed_chunks, "_value"):
        return int(rag_indexed_chunks._value.get())
    return 0
