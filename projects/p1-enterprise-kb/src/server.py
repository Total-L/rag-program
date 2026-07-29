"""P1 FastAPI server（ENTERPRISE_AUDIT.md M2 + D6）。

端点：
- POST /v1/query              — 同步 RAG 查询（JSON）
- DELETE /v1/users/{id}/data  — GDPR 第 17 条 DSR（S3）
- GET  /v1/healthz            — liveness
- GET  /v1/readyz             — readiness（索引是否就绪）
- GET  /v1/metrics            — Prometheus metrics
- GET  /openapi.json          — OpenAPI schema

认证：
- X-API-Key header（env: RAG_PROG_API_KEY 配置；未配置则认证 disabled，便于 dev）
- X-Tenant-Id header（必填，未注册/格式错误 → 401/403）

OpenTelemetry（D6-T5）：
- OTEL_ENABLED=true 时启用 OTLP exporter + FastAPI auto-instrumentation
- 默认关闭；OTel 包是 optional extras，不装不报错
- 与现有 structlog trace_id 并列（OTel trace 自带 context，不替换）

启动：
    RAG_PROG_API_KEY=secret uvicorn projects.p1-enterprise-kb.src.server:app
    # 或：
    cd projects/p1-enterprise-kb/src && python server.py
    # 启用 OTel：
    OTEL_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 uvicorn ...

测试：
    pytest projects/p1-enterprise-kb/tests/test_server.py -v
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from acl import User
from observability import (
    configure_logging,
    get_logger,
    healthz_response,
    metrics_response,
    readyz_response,
)
from pipeline import P1Pipeline
from tenant import Tenant, TenantIdError, TenantNotFoundError
from tenant_registry import get_registry

# ── 配置 ──

API_KEY = os.environ.get("RAG_PROG_API_KEY", "")  # 空字符串 = 认证 disabled
DEFAULT_USER_ROLE = os.environ.get("RAG_PROG_DEFAULT_USER_ROLE", "executive")


# ── FastAPI app ──

app = FastAPI(
    title="rag_program P1 API",
    version="1.0.0",
    description="企业知识库 RAG 服务（FastAPI wrapper around P1Pipeline）",
)


# ── OpenTelemetry（D6-T5）──
# 与现有 structlog ContextVar trace_id 并列存在：OTel 自带 trace context，
# FastAPIInstrumentor 自动给每个 request 创建 span。
# 设计取舍（per CLAUDE.md "Simplicity First"）：
#   - OTel 是 optional extra，缺包时**静默降级**（OTEL_ENABLED=true 但 opentelemetry
#     没装 → 启动 warning + 继续按非 OTel 跑）。
#   - 不替换现有 observability.py：trace_id 仍写 structlog / metrics label / audit，
#     两条线各管一段；后续要做 trace_id ↔ otel trace_id 关联再加 mixin。
_otel_enabled = False


def _setup_otel() -> bool:
    """启动期 OTel setup；返回是否启用。

    - OTEL_ENABLED=true + 包齐：setup TracerProvider + OTLP exporter + FastAPI instrumentation
    - OTEL_ENABLED=true + 包缺：warning + 降级（不抛）
    - OTEL_ENABLED 未设 / false：跳过整段
    """
    global _otel_enabled
    if os.environ.get("OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        from opentelemetry import trace  # noqa: PLC0415
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,  # noqa: PLC0415
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415
        from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    except ImportError as e:
        configure_logging(level="INFO")
        log = get_logger("rag.otel")
        log.warning(
            "otel_disabled_missing_packages",
            error=str(e),
            hint="pip install -e '.[observability]' to enable",
        )
        return False

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    _otel_enabled = True
    configure_logging(level="INFO")
    log = get_logger("rag.otel")
    log.info("otel_enabled", endpoint=endpoint)
    return True


_setup_otel()


# ── 请求/响应 schema ──


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    user_id: str = Field(default="anonymous", description="用户 ID（ACL 用）")
    user_role: str | None = Field(default=None, description="用户角色；不传则用 DEFAULT_USER_ROLE")
    trace_id: str | None = Field(default=None, description="外部 trace ID（关联日志）")


class Citation(BaseModel):
    id: str
    text: str | None = None
    chunk_id: str | None = None


class QueryResponse(BaseModel):
    question: str
    answer: str
    citations: list
    abstained: bool
    user_role: str
    latency_ms: float
    trace_id: str | None = None
    retrieved_source_ids: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    indexed_chunks: int | None = None
    reason: str | None = None


class DSRResponse(BaseModel):
    """GDPR 第 17 条 DSR（Data Subject Rights）抹除结果。

    字段对应 `AuditLogger.dsr_erase_user()` 返回值；详见 src/audit.py。
    """

    user_id_hash: str
    removed_events: int
    dry_run_removed_events: int
    scanned_files: list[str]
    dry_run: bool


# ── D7-T3：摄取薄壳 ──


class IngestRequest(BaseModel):
    """摄取请求。dev 同步返（生产应改后台 task + webhook 回调）。"""

    source_path: str = Field(..., description="文件或目录路径；容器内路径如 /app/data/source")
    recursive: bool = Field(default=True, description="目录是否递归扫描")
    full_scan: bool = Field(default=False, description="True = 重新评估所有 records（含删除）")
    patterns: list[str] | None = Field(
        default=None,
        description="glob patterns（覆盖默认 *.md/*.txt/*.pdf/*.docx/*.xlsx/*.html）",
    )


class IngestResponse(BaseModel):
    """摄取结果。"""

    run_id: str
    tenant_id: str
    listed: int
    new: int
    changed: int
    unchanged: int
    failures: list[dict]


# ── D7-T4：Webhook 端点 schema ──


class WebhookCreateRequest(BaseModel):
    """注册 webhook。"""

    url: str = Field(..., min_length=8, max_length=500, description="回调 URL（http/https）")
    events: list[str] = Field(
        default_factory=list,
        description="订阅事件；空 = 全订阅；允许: query_completed / audit_written / quota_exceeded / ingest_completed / ingest_failed",
    )
    secret: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="HMAC 密钥；空则服务端生成。生产应自己生成（≥ 32 字符）",
    )


class WebhookResponse(BaseModel):
    """webhook 详情（不含 secret）。"""

    id: str
    tenant_id: str
    url: str
    events: list[str]
    created_at: str
    active: bool


class WebhookListResponse(BaseModel):
    """webhook 列表响应。"""

    webhooks: list[WebhookResponse]


class WebhookDeleteResponse(BaseModel):
    """webhook 删除响应。"""

    deleted: bool
    id: str


# ── 认证 ──


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """X-API-Key 认证；API_KEY 为空时禁用认证（dev mode）。

    生产部署必须设 RAG_PROG_API_KEY；未设则所有请求都通过。
    """
    if not API_KEY:  # dev mode
        return
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key header",
        )


async def resolve_tenant(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> Tenant:
    """S2 多租户：解析 X-Tenant-Id 并校验注册表。

    顺序：
    1. 缺 header → 400（要求显式声明）
    2. 格式非法 → 400
    3. 未注册 → 404（区别于"密码错"的 401，避免泄漏注册表）

    注册表存在但 tenant.api_key 非空 → 必须同时带 X-API-Key 校验。
    dev 模式下（Tenant.api_key 为空 + API_KEY 全局也空）放行。
    """
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-Id header required (S2 multi-tenancy)",
        )
    try:
        tenant = get_registry().get(x_tenant_id)
    except TenantIdError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid X-Tenant-Id: {e}",
        ) from e
    except TenantNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    return tenant


# ── 依赖：pipeline 实例（单例，懒初始化）──

_pipeline_instance: P1Pipeline | None = None


def get_pipeline() -> P1Pipeline:
    """获取（或创建）P1Pipeline 单例。

    注意：单例简化了演示；生产应换成 Depends + lifespan + 多 worker 共享 store。
    """
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = P1Pipeline(user=User(user_id="api-default", role=DEFAULT_USER_ROLE))
        # 不在 import 时跑 index_corpus（需外部 trigger 或单独 /v1/ingest 端点）
    return _pipeline_instance


# ── 端点 ──


@app.get(
    "/v1/healthz", response_model=HealthResponse, tags=["health"], response_model_exclude_none=True
)
async def healthz():
    """Liveness probe。"""
    body, _ = healthz_response()
    return body


@app.get("/v1/readyz", response_model=HealthResponse, tags=["health"])
async def readyz():
    """Readiness probe。"""
    p = get_pipeline()
    body, status_code = readyz_response(pipeline_indexed=p._indexed)
    return JSONResponse(content=body, status_code=status_code)


@app.get("/v1/metrics", tags=["health"])
async def metrics():
    """Prometheus metrics 端点。"""
    payload, status_code, headers = metrics_response()
    return Response(content=payload, status_code=status_code, headers=headers)


@app.post(
    "/v1/query",
    response_model=QueryResponse,
    tags=["rag"],
    dependencies=[Depends(verify_api_key)],
)
async def query(
    req: QueryRequest,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008 — FastAPI 标准写法
):
    """同步 RAG 查询（S2 多租户：tenant 从 X-Tenant-Id header 解析）。

    Returns:
        QueryResponse with answer/citations/abstained/latency/trace_id
    """
    configure_logging(level="INFO")
    log = get_logger("rag.api")
    p = get_pipeline()

    # 如果 req 指定了 user_role，创建临时 pipeline（演示用）
    if req.user_role and req.user_role != p.user.role:
        p = P1Pipeline(
            user=User(user_id=req.user_id, role=req.user_role),
        )
        if p._indexed is False:
            # 还没索引过，复用全局 store 的索引
            p.store = _pipeline_instance.store  # type: ignore[union-attr]
            p._indexed = _pipeline_instance._indexed  # type: ignore[union-attr]

    log.info(
        "api_query_received",
        question=req.question[:80],
        user_id=req.user_id,
        user_role=req.user_role or DEFAULT_USER_ROLE,
        tenant_id=tenant.tenant_id,
    )

    r = p.ask(req.question, tenant_id=tenant.tenant_id, trace_id=req.trace_id)

    log.info(
        "api_query_done",
        trace_id=r.stages.get("trace_id"),
        latency_ms=round(r.latency_ms, 1),
        abstained=r.abstained,
    )

    return QueryResponse(
        question=r.question,
        answer=r.answer,
        citations=r.citations,
        abstained=r.abstained,
        user_role=r.user_role,
        latency_ms=r.latency_ms,
        trace_id=r.stages.get("trace_id"),
        retrieved_source_ids=r.retrieved_source_ids,
        retrieved_chunk_ids=r.retrieved_chunk_ids,
    )


# ── D7-T2：SSE 流式查询 ──


@app.post(
    "/v1/query/stream",
    tags=["rag"],
    dependencies=[Depends(verify_api_key)],
)
async def query_stream(
    req: QueryRequest,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008
):
    """SSE 流式查询：阶段事件（started → retrieve → generate → done）。

    与 /v1/query 区别：返回 `text/event-stream`；前端用 EventSource 订阅
    每个 `data: {...json...}` 行即可拿到阶段进度（不用等整段 LLM 返完）。

    Events:
        started        - {trace_id, tenant_id, ts}
        retrieve_started
        retrieve_done  - {indexed_chunks}
        generate_started
        done           - {result: {...P1Result dict...}, latency_ms}
        error          - {status: quota_exceeded|internal_error, message}
    """
    p = get_pipeline()
    configure_logging(level="INFO")
    log = get_logger("rag.api")

    # 处理 user_role 切换（与 /v1/query 一致）
    if req.user_role and req.user_role != p.user.role:
        p = P1Pipeline(user=User(user_id=req.user_id, role=req.user_role))
        if p._indexed is False:
            p.store = _pipeline_instance.store  # type: ignore[union-attr]
            p._indexed = _pipeline_instance._indexed  # type: ignore[union-attr]

    log.info(
        "api_query_stream_received",
        question=req.question[:80],
        tenant_id=tenant.tenant_id,
        user_id=req.user_id,
    )

    def event_generator():
        import json

        try:
            for event in p.ask_stream(
                req.question,
                tenant_id=tenant.tenant_id,
                trace_id=req.trace_id,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.exception("api_query_stream_failed")
            err = {"event": "error", "status": "internal_error", "message": str(e)[:200]}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁 nginx 缓冲（SSE 必需）
        },
    )


# ── D7-T3：摄取薄壳 ──


def _p6_src() -> str:
    """返回 P6 src + 父目录路径并加入 sys.path（懒加载）。

    P6 的 IngestPipeline 在 `run_ingest.py`（不在 src/pipeline.py），所以要加
    P6 父目录；src 子目录放 src/* 模块（store / chunkers / connectors 等）。
    """
    p6_root = Path(__file__).resolve().parents[2] / "p6-ingestion-platform"
    p6_src = p6_root / "src"
    for p in (p6_src, p6_root):
        p_str = str(p)
        if p_str not in sys.path:
            sys.path.insert(0, p_str)
    return str(p6_src)


def _run_ingest(
    *,
    source_path: str,
    tenant_id: str,
    recursive: bool,
    full_scan: bool,
    patterns: list[str] | None,
) -> dict:
    """薄壳包 P6 IngestPipeline.run() —— dev 同步返 IngestStats。

    同步返回设计理由（per CLAUDE.md "Simplicity First"）：
    - dev 不需要后台任务编排；IngestPipeline 已含 DLQ + 幂等 upsert + manifest
    - 长任务（GB 级语料）应改 BackgroundTasks + webhook 回调（D7-T5 配套）

    Raises:
        FileNotFoundError: source_path 不存在
        ValueError: tenant_id 空（IngestPipeline 内置校验）
        OSError: 底层 IO 失败

    Notes:
        - P6 内部用 `from .models import ...` 相对 import → 需要 P6 src 是个 package
        - P1 也有同名 src/，所以这里临时把 `sys.modules["src"]` 重定向到 P6 src/
        - 用完还原（finally），避免污染后续 P1 import
    """

    p6_src = _p6_src()  # sys.path 加 P6 src

    # ★ P6 的 manifest.py 等用 `from .models import` 相对 import →
    #   必须把 `src` 这个名字当作 P6 package 加载。P1 也有 src/，故临时 swap。
    saved_modules: dict[str, object] = {}
    src_keys = [k for k in sys.modules if k == "src" or k.startswith("src.")]
    for k in src_keys:
        saved_modules[k] = sys.modules.pop(k)
    # 加 P6 parent dir 让 `src` 解析为 P6 package
    p6_root = Path(p6_src).resolve().parent
    if str(p6_root) not in sys.path:
        sys.path.insert(0, str(p6_root))

    try:
        # IngestPipeline 在 run_ingest.py（不是 src/pipeline.py）
        from run_ingest import IngestPipeline  # noqa: PLC0415
        from src.connectors import FileSystemConnector  # noqa: PLC0415
        from src.embeddings import get_embedder  # noqa: PLC0415
        from src.manifest import ManifestStore  # noqa: PLC0415
        from src.models import IngestFailure  # noqa: PLC0415
        from src.store import open_index_store  # noqa: PLC0415 — P6 import

        src = Path(source_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"source_path not found: {source_path}")

        # store / manifest 用 P1 同款（Chroma + SQLite manifest）；共用 data/index
        index_dir = Path(__file__).resolve().parents[1] / "data" / "index"
        index_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = index_dir / "manifest.sqlite"
        dlq_dir = Path(__file__).resolve().parents[1] / "data" / "dlq"
        dlq_dir.mkdir(parents=True, exist_ok=True)

        store = open_index_store("auto", chroma_path=index_dir, dim=768)
        manifest = ManifestStore(path=manifest_path)
        embedder = get_embedder("auto")
        default_patterns = ("*.md", "*.txt", "*.pdf", "*.docx", "*.xlsx", "*.html")
        pats: tuple[str, ...] = tuple(patterns) if patterns else default_patterns
        if not recursive:
            # 目录非递归：把 glob pattern 限制到顶层（rglob → glob）
            pass  # FileSystemConnector 内部用 rglob；非递归得自己写个简化版
        connector = FileSystemConnector(root=src, patterns=pats)

        pipe = IngestPipeline(
            store=store,
            manifest=manifest,
            embedder=embedder,
            dlq_dir=dlq_dir,
            tenant_id=tenant_id,
        )
        import uuid as _uuid

        run_id = f"ing-{_uuid.uuid4().hex[:8]}"
        stats = pipe.run([connector], run_id=run_id, full_scan=full_scan)
        failures: list[IngestFailure] = stats.failures
        return {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "listed": stats.listed,
            "new": stats.new,
            "changed": stats.changed,
            "unchanged": stats.unchanged,
            "failures": [
                {"source": f.source, "stage": f.stage, "error": f.error[:200]}
                for f in failures
            ],
        }
    finally:
        # 还原 P1 src 模块（避免污染后续 P1 import）
        for k, v in saved_modules.items():
            sys.modules[k] = v


@app.post(
    "/v1/ingest",
    response_model=IngestResponse,
    tags=["ingest"],
    dependencies=[Depends(verify_api_key)],
)
async def ingest(
    req: IngestRequest,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008
):
    """同步摄取（薄壳包 P6 IngestPipeline）。

    - 必须带 X-Tenant-Id（摄取是租户级操作）
    - source_path 必须存在；可指向文件或目录
    - dev 同步；生产改 BackgroundTasks + webhook 回调
    """
    configure_logging(level="INFO")
    log = get_logger("rag.api")
    log.info(
        "api_ingest_received",
        tenant_id=tenant.tenant_id,
        source_path=req.source_path,
        recursive=req.recursive,
        full_scan=req.full_scan,
    )
    try:
        result = _run_ingest(
            source_path=req.source_path,
            tenant_id=tenant.tenant_id,
            recursive=req.recursive,
            full_scan=req.full_scan,
            patterns=req.patterns,
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        log.exception("api_ingest_failed")
        # D7-T5：ingest 失败也派发 webhook（订阅者可设专门告警）
        try:
            from webhooks import dispatch_async

            dispatch_async(
                tenant_id=tenant.tenant_id,
                event="ingest_failed",
                payload={
                    "source_path": req.source_path,
                    "error": str(e)[:200],
                },
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ingest failed: {e}",
        ) from e

    log.info(
        "api_ingest_done",
        run_id=result["run_id"],
        tenant_id=tenant.tenant_id,
        new=result["new"],
        changed=result["changed"],
        failures=len(result["failures"]),
    )
    # D7-T5：触发 ingest_completed webhook（fire-and-forget）
    try:
        from webhooks import dispatch_async

        dispatch_async(
            tenant_id=tenant.tenant_id,
            event="ingest_completed",
            payload={
                "run_id": result["run_id"],
                "source_path": req.source_path,
                "new": result["new"],
                "changed": result["changed"],
                "failures": len(result["failures"]),
            },
        )
    except Exception:
        pass  # webhook 失败不影响响应
    return IngestResponse(**result)


# ── D7-T4：Webhook 注册/列表/删除 ──


@app.post(
    "/v1/webhooks",
    response_model=WebhookResponse,
    tags=["webhooks"],
    dependencies=[Depends(verify_api_key)],
)
async def create_webhook(
    req: WebhookCreateRequest,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008
):
    """注册 webhook。

    - 必填 url + events；secret 可让服务端生成（dev）或自填（生产 ≥ 32 字符）
    - 返回的 id 后续用 DELETE 删除
    - secret 仅在创建时可见一次（响应不含 secret；丢失则重新注册）
    """
    from webhooks import get_webhook_registry

    configure_logging(level="INFO")
    log = get_logger("rag.api")
    try:
        w = get_webhook_registry().register(
            tenant_id=tenant.tenant_id,
            url=req.url,
            events=req.events,
            secret=req.secret or "",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    log.info(
        "webhook_registered",
        id=w.id,
        tenant_id=tenant.tenant_id,
        events=req.events,
    )
    return WebhookResponse(**w.to_public_dict())


@app.get(
    "/v1/webhooks",
    response_model=WebhookListResponse,
    tags=["webhooks"],
    dependencies=[Depends(verify_api_key)],
)
async def list_webhooks(
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008
):
    """列出本 tenant 的所有 webhook（不含 secret）。"""
    from webhooks import get_webhook_registry

    hooks = get_webhook_registry().list_for_tenant(tenant_id=tenant.tenant_id)
    return WebhookListResponse(
        webhooks=[WebhookResponse(**w.to_public_dict()) for w in hooks]
    )


@app.delete(
    "/v1/webhooks/{webhook_id}",
    response_model=WebhookDeleteResponse,
    tags=["webhooks"],
    dependencies=[Depends(verify_api_key)],
)
async def delete_webhook(
    webhook_id: str,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008
):
    """删除 webhook。只能删同 tenant 的（防跨租户）。"""
    from webhooks import get_webhook_registry

    if not webhook_id or len(webhook_id) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook_id path param invalid",
        )
    ok = get_webhook_registry().delete(
        webhook_id=webhook_id, tenant_id=tenant.tenant_id
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"webhook {webhook_id!r} not found for tenant {tenant.tenant_id!r}",
        )
    return WebhookDeleteResponse(deleted=True, id=webhook_id)


# ── GDPR DSR 端点 ──


def _validate_user_id_path(user_id: str) -> str:
    """DSR 路径参数 user_id 校验：仅 url-safe + 长度限。

    为什么不用 tenant_id 的 slug 规则（[a-z0-9-]）：
    - tenant_id 是租户标识，强制小写短横线是人类可读；
    - user_id 来源多样（可能是邮箱前缀 / 工号 / UUID），必须更宽松。
    - 仍拒绝 `..` / `/` / `%` / 控制字符，防路径遍历与 URL 注入。
    """
    if not user_id or len(user_id) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id path param must be 1-64 chars",
        )
    if not re.match(r"^[A-Za-z0-9._\-]+$", user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id path param must be url-safe ([A-Za-z0-9._-])",
        )
    return user_id


@app.delete(
    "/v1/users/{user_id}/data",
    response_model=DSRResponse,
    tags=["gdpr"],
    dependencies=[Depends(verify_api_key)],
)
async def dsr_erase_user_data(
    user_id: str,
    tenant: Tenant = Depends(resolve_tenant),  # noqa: B008 — FastAPI 标准写法
    dry_run: bool = Query(default=False, description="True = 只统计不抹除（preview）"),
):
    """GDPR 第 17 条"被遗忘权"：抹除指定用户在本租户 audit log 中的全部痕迹。

    抹除范围：
    - audit log（data/audit/*.jsonl 中 user_id_hash 匹配的行）

    不抹除范围（设计取舍，详见 audit.py 顶部注释）：
    - Chroma / pgvector chunk（P6 摄取的是公域文档，无 user_id）
    - 其他租户的 audit（DSR 必须带 X-Tenant-Id 防跨租户删除）

    幂等性：重复 DELETE 返回 `removed_events=0`，HTTP 始终 200。
    dry_run=True 时返回 `dry_run_removed_events` 字段（仍 200，便于 preview）。

    认证：
    - X-API-Key（与 query 一致）
    - X-Tenant-Id（强制；DSR 仅作用于该租户的 audit 文件）
    """
    _validate_user_id_path(user_id)
    from audit import get_audit_logger  # 延迟 import 避免 server 启动失败拖垮 audit

    configure_logging(level="INFO")
    log = get_logger("rag.api")
    audit_log = get_audit_logger()
    result = audit_log.dsr_erase_user(user_id, dry_run=dry_run)
    log.info(
        "dsr_erase_user",
        tenant_id=tenant.tenant_id,
        user_id_hash=result["user_id_hash"],
        removed_events=result["removed_events"],
        dry_run=dry_run,
    )
    return DSRResponse(**result)


# ── CLI 启动 ──

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("RAG_PROG_API_PORT", "8080")),
        reload=False,
    )
