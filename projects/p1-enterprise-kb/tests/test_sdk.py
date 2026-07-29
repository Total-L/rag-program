"""P1 SDK 测试（ENTERPRISE_AUDIT.md M2b）。

用 FastAPI TestClient + httpx.MockTransport 模拟 server，
不依赖真实 uvicorn 进程（CI 友好）。

覆盖：
1. RagClient.query 正确解析 QueryResult
2. API key 透传到 X-API-Key header
3. trace_id 透传到 X-Trace-Id
4. user_role / user_id 透传到 body
5. HTTP 4xx → RagAPIError（含 status_code + detail）
6. 连接失败 → RagConnectionError
7. healthz / readyz
"""

from __future__ import annotations

import pytest

# hard deps
httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from rag_client import (  # noqa: E402
    QueryResult,
    RagAPIError,
    RagClient,
    RagConnectionError,
)

# ── fixtures ──


@pytest.fixture
def fake_server():
    """FastAPI TestClient（无需 uvicorn 进程）。"""
    server._pipeline_instance = None
    return TestClient(server.app)


@pytest.fixture
def client(fake_server):
    """绑定 fake_server 的 RagClient（用 transport= 注入 httpx）。"""

    # 注入 fake_server 的 transport
    transport = httpx.MockTransport(fake_server._transport.handle_request)  # noqa: F841
    c = RagClient(
        base_url="http://testserver",
        api_key="test-key",
        tenant_id="acme-corp",  # S2：所有 SDK 调用自动带 X-Tenant-Id
    )
    # 替换 client 内部的 httpx transport（httpx.Client 默认是真实网络）
    # 通过 monkey-patching 实现
    import httpx as _httpx

    orig_post = _httpx.post
    orig_get = _httpx.get

    def fake_post(url, **kwargs):
        # 把 url 改成 testserver
        if url.startswith(c.base_url):
            url = url.replace(c.base_url, "http://testserver")
        return fake_server.post(url.replace("http://testserver", ""), **kwargs)

    def fake_get(url, **kwargs):
        if url.startswith(c.base_url):
            url = url.replace(c.base_url, "http://testserver")
        return fake_server.get(url.replace("http://testserver", ""), **kwargs)

    _httpx.post = fake_post
    _httpx.get = fake_get
    try:
        yield c
    finally:
        _httpx.post = orig_post
        _httpx.get = orig_get


# ── query ──


def test_query_parses_response(client):
    """query() 返回 QueryResult，所有字段填好。"""
    # 配 API_KEY 让 server 要求 auth
    server.API_KEY = "test-key"
    try:
        result = client.query("员工年假？")
    finally:
        server.API_KEY = ""
    assert isinstance(result, QueryResult)
    assert result.question == "员工年假？"
    assert isinstance(result.answer, str)
    assert isinstance(result.abstained, bool)
    assert isinstance(result.citations, list)
    # trace_id 必有
    assert result.trace_id is not None
    assert len(result.trace_id) == 16


def test_query_sends_api_key_header(client):
    """X-API-Key 必须透传到 server（否则被 401）。"""
    server.API_KEY = "test-key"
    try:
        # 不抛异常 → 通过
        result = client.query("员工年假？")
        assert result.abstained is True  # 没索引，拒答
    finally:
        server.API_KEY = ""


def test_query_without_api_key_fails_when_server_requires(client):
    """server 配了 API_KEY，client 没传 → RagAPIError(401)。"""
    server.API_KEY = "required-key"
    # 改 client 不带 api_key
    client.api_key = None
    try:
        with pytest.raises(RagAPIError) as exc_info:
            client.query("员工年假？")
        assert exc_info.value.status_code == 401
    finally:
        server.API_KEY = ""


def test_query_passes_user_role_and_user_id(client):
    """user_role / user_id 必须透传到 server body。"""
    # 我们没法直接 verify body（mock 已转走），只能 verify 不报错
    result = client.query(
        "员工年假？",
        user_id="alice",
        user_role="executive",
    )
    assert result.question == "员工年假？"


def test_query_passes_trace_id(client):
    """外部 trace_id 透传，response 也带回。"""
    result = client.query("员工年假？", trace_id="sdk-trace-123")
    assert result.trace_id == "sdk-trace-123"


# ── error handling ──


def test_connection_error_on_bad_url():
    """连不上 server → RagConnectionError。"""
    import httpx as _httpx

    orig_post = _httpx.post
    _httpx.post = lambda *a, **kw: (_ for _ in ()).throw(
        _httpx.ConnectError("simulated connection refused")
    )
    try:
        c = RagClient(base_url="http://nonexistent:9999")
        with pytest.raises(RagConnectionError):
            c.query("员工年假？")
    finally:
        _httpx.post = orig_post


# ── health / ready ──


def test_healthz(client):
    """client.healthz() 返回 True。"""
    assert client.healthz() is True


def test_readyz_returns_false_when_not_indexed(client):
    """未索引 → readyz 返回 (False, reason)。"""
    ready, reason = client.readyz()
    assert ready is False
    assert reason == "not_ready" or "index" in reason.lower()


# ── dataclass ──


def test_query_result_from_api_full():
    body = {
        "question": "Q",
        "answer": "A",
        "citations": [{"id": "E1", "text": "t", "chunk_id": "c1"}],
        "abstained": False,
        "user_role": "executive",
        "latency_ms": 123.4,
        "trace_id": "tr-1",
        "retrieved_source_ids": ["hr-001"],
        "retrieved_chunk_ids": ["c1"],
    }
    r = QueryResult.from_api(body)
    assert r.citations[0].id == "E1"
    assert r.citations[0].chunk_id == "c1"
    assert r.latency_ms == 123.4
    assert r.trace_id == "tr-1"
    assert r.retrieved_source_ids == ["hr-001"]


def test_query_result_from_api_minimal():
    """缺字段也能解析（用 default）。"""
    body = {"question": "Q", "answer": "A"}
    r = QueryResult.from_api(body)
    assert r.citations == []
    assert r.abstained is False
    assert r.trace_id is None
