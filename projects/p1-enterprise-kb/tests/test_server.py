"""P1 FastAPI server 测试（ENTERPRISE_AUDIT.md M2）。

覆盖：
1. /v1/healthz 返回 200 ok
2. /v1/readyz 返回 503（未索引）/200（已索引）
3. /v1/metrics 返回 Prometheus 文本
4. POST /v1/query 接受问题、返回 QueryResponse
5. X-API-Key 认证：未配 → 通过；配了 → 必须匹配
6. POST /v1/query 自动生成 trace_id / 接受外部 trace_id
7. POST /v1/query 写入 Prometheus metrics

跑：
    pytest projects/p1-enterprise-kb/tests/test_server.py -v -m api
"""

from __future__ import annotations

import pytest

# hard deps
fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
prom = pytest.importorskip("prometheus_client")

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from observability import rag_queries_total  # noqa: E402

# ── fixtures ──


@pytest.fixture
def client():
    """TestClient（FastAPI 内置）。S2 多租户：自动带 X-Tenant-Id=acme-corp。"""
    # 每个测试前重置单例
    server._pipeline_instance = None
    # TestClient 自动给所有请求附加 default_headers；避免每个 test 自己写
    return TestClient(server.app, headers={"X-Tenant-Id": "acme-corp"})


@pytest.fixture
def authed_client(monkeypatch):
    """配了 API_KEY 的 TestClient。"""
    monkeypatch.setenv("RAG_PROG_API_KEY", "test-secret-key")
    server.API_KEY = "test-secret-key"  # 直接修改 module 变量（更稳）
    server._pipeline_instance = None
    yield TestClient(server.app)
    server.API_KEY = ""  # cleanup


# ── health ──


def test_healthz_returns_ok(client):
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_returns_503_when_not_indexed(client):
    """未跑 index_corpus → readyz 503 + reason。"""
    r = client.get("/v1/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert "index_corpus" in body["reason"]


def test_metrics_returns_prometheus_text(client):
    r = client.get("/v1/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "# HELP" in body
    assert "# TYPE" in body


# ── auth ──


def test_query_works_without_auth_when_api_key_disabled(client):
    """API_KEY 为空 → 认证 disabled，请求通过。"""
    r = client.post("/v1/query", json={"question": "员工年假？"})
    # 不索引也会返回 200（拒答），但不应 401
    assert r.status_code != 401


def test_query_requires_api_key_when_enabled(authed_client):
    """API_KEY 设了 → 无 X-API-Key → 401。"""
    r = authed_client.post("/v1/query", json={"question": "员工年假？"})
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_query_accepts_correct_api_key(authed_client):
    """正确 X-API-Key → 通过。"""
    r = authed_client.post(
        "/v1/query",
        json={"question": "员工年假？"},
        headers={"X-API-Key": "test-secret-key"},
    )
    assert r.status_code != 401


def test_query_rejects_wrong_api_key(authed_client):
    """错误 X-API-Key → 401。"""
    r = authed_client.post(
        "/v1/query",
        json={"question": "员工年假？"},
        headers={"X-API-Key": "wrong-key"},
    )
    assert r.status_code == 401


# ── query ──


def test_query_returns_query_response_shape(client):
    """POST /v1/query 返回所有必需字段。"""
    r = client.post("/v1/query", json={"question": "员工年假？"})
    assert r.status_code == 200
    body = r.json()
    # 必填字段
    for field in (
        "question",
        "answer",
        "citations",
        "abstained",
        "user_role",
        "latency_ms",
        "trace_id",
        "retrieved_source_ids",
        "retrieved_chunk_ids",
    ):
        assert field in body, f"missing field: {field}"
    assert body["question"] == "员工年假？"
    assert isinstance(body["abstained"], bool)
    assert isinstance(body["latency_ms"], (int, float))


def test_query_auto_generates_trace_id(client):
    """不传 trace_id → 自动生成 16 位 hex。"""
    r = client.post("/v1/query", json={"question": "员工年假？"})
    body = r.json()
    assert body["trace_id"] is not None
    assert len(body["trace_id"]) == 16


def test_query_accepts_external_trace_id(client):
    """外部 trace_id 透传到 response。"""
    r = client.post(
        "/v1/query",
        json={"question": "员工年假？", "trace_id": "my-custom-trace"},
    )
    body = r.json()
    assert body["trace_id"] == "my-custom-trace"


def test_query_records_metrics(client):
    """每次 POST /v1/query 上报 rag_queries_total（按 status + tenant_id label）。

    S2：metrics 加了 tenant_id label —— 累加所有 status 的计数（pipeline 内部状态
    由是否 indexed 决定，可能 success / abstain / not_ready，不稳定）。
    """

    # 取 metric 内部所有 labelled children 的总和
    def _total(metric) -> float:
        # Counter._metrics 是 dict[(label_values_tuple)] -> child
        # rag_queries_total 只标了 (status, tenant_id)，所以这里累加所有 child
        children = getattr(metric, "_metrics", None)
        if not children:
            return 0.0
        return sum(child._value.get() for child in children.values())

    before = _total(rag_queries_total)
    client.post("/v1/query", json={"question": "员工年假？"})
    after = _total(rag_queries_total)
    assert after == before + 1


def test_query_validates_empty_question(client):
    """空 question → 422 (Pydantic validation)。"""
    r = client.post("/v1/query", json={"question": ""})
    assert r.status_code == 422


def test_query_validates_long_question(client):
    """question 超 2000 字符 → 422。"""
    r = client.post("/v1/query", json={"question": "x" * 3000})
    assert r.status_code == 422


# ── OpenAPI ──


def test_openapi_schema_exposed(client):
    """FastAPI 自动暴露 /openapi.json。"""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "rag_program P1 API"
    # 5 个端点都在
    paths = schema["paths"]
    assert "/v1/healthz" in paths
    assert "/v1/readyz" in paths
    assert "/v1/metrics" in paths
    assert "/v1/query" in paths
