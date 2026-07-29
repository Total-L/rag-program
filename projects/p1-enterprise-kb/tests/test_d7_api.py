"""P1 — D7 API 集成测试（ENTERPRISE_AUDIT D7）。

覆盖：
1. SSE /v1/query/stream 返回 text/event-stream + 阶段事件序列
2. /v1/ingest happy path + FileNotFound
3. /v1/webhooks 注册 / 列表 / 删除 + 跨租户保护
4. webhook HMAC 签名校验（dispatch_async 真实派发）
5. dispatch_async fire-and-forget（不阻塞主流程）

跑：
    pytest projects/p1-enterprise-kb/tests/test_d7_api.py -v
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# ── fixtures ──


@pytest.fixture
def client_with_index(monkeypatch):
    """TestClient + 跳过 health/ready 校验（pipeline 默认 _indexed=False）。"""
    server._pipeline_instance = None
    return TestClient(server.app, headers={"X-Tenant-Id": "acme-corp"})


@pytest.fixture
def tmp_webhooks_path(tmp_path: Path, monkeypatch):
    """每个测试一个独立 webhooks.json（避免污染）。"""
    p = tmp_path / "webhooks.json"
    monkeypatch.setenv("WEBHOOKS_PATH", str(p))
    import webhooks

    webhooks.reset_webhook_registry()
    yield p
    webhooks.reset_webhook_registry()


# ── 1. SSE 流式查询 ──


def test_sse_returns_event_stream_content_type(client_with_index):
    """POST /v1/query/stream 返回 text/event-stream。"""
    r = client_with_index.post("/v1/query/stream", json={"question": "test"})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    # 不应缓存
    assert r.headers.get("cache-control") == "no-cache"


def test_sse_emits_stage_events(client_with_index):
    """SSE 事件序列包含 started / retrieve_done / generate_started / done（或 error）。"""
    r = client_with_index.post("/v1/query/stream", json={"question": "test"})
    assert r.status_code == 200
    body = r.text

    # 每行都是 `data: {...}\n\n`
    events = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            events.append(payload)

    # 至少包含 started 和终结事件（done 或 error）
    event_types = [e.get("event") for e in events]
    assert "started" in event_types, f"缺少 started 事件：{event_types}"
    last = events[-1]
    assert last.get("event") in ("done", "error"), f"末事件应是 done/error：{last}"


def test_sse_propagates_trace_id(client_with_index):
    """SSE 事件应携带 trace_id（与 request 传入的一致）。"""
    r = client_with_index.post(
        "/v1/query/stream",
        json={"question": "test", "trace_id": "my-trace-001"},
    )
    body = r.text
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    # started 事件必含 trace_id
    assert events[0].get("trace_id") == "my-trace-001"


def test_sse_requires_tenant(client_with_index):
    """缺 X-Tenant-Id → 400（SSE 端点同样要求 tenant）。"""
    no_tenant = TestClient(server.app)  # 不带 X-Tenant-Id
    r = no_tenant.post("/v1/query/stream", json={"question": "test"})
    assert r.status_code == 400
    assert "X-Tenant-Id" in r.json()["detail"]


def test_sse_handles_quota_exceeded(client_with_index, monkeypatch):
    """quota_exceeded 路径应 yield error 事件而非 raise 5xx。"""
    # 用配额耗尽的 mock tenant
    from quota_store import InMemoryQuotaStore
    from tenant import Quota, Tenant
    from tenant_registry import TenantRegistry

    tenant = Tenant(
        tenant_id="acme-corp",
        name="X",
        quota=Quota(max_queries_per_day=0, max_tokens_per_day=0, max_chunks_indexed=0),
    )
    quota = InMemoryQuotaStore()

    class _Reg(TenantRegistry):
        def get(self, tid):  # type: ignore[override]
            if tid == "acme-corp":
                return tenant
            raise KeyError(tid)

        def all(self):  # type: ignore[override]
            return [tenant]

    import pipeline
    import quota_store
    import tenant_registry

    monkeypatch.setattr(pipeline, "get_registry", lambda: _Reg())
    monkeypatch.setattr(pipeline, "get_quota_store", lambda: quota)
    monkeypatch.setattr(tenant_registry, "get_registry", lambda: _Reg())
    monkeypatch.setattr(quota_store, "get_quota_store", lambda: quota)

    r = client_with_index.post("/v1/query/stream", json={"question": "test"})
    assert r.status_code == 200
    body = r.text
    assert '"event": "error"' in body
    assert '"status": "quota_exceeded"' in body


# ── 2. /v1/ingest 薄壳 ──


def test_ingest_returns_400_for_nonexistent_path(client_with_index):
    """source_path 不存在 → 400。"""
    r = client_with_index.post(
        "/v1/ingest",
        json={"source_path": "/nonexistent/path/that/does/not/exist"},
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"].lower()


def test_ingest_requires_tenant():
    """缺 X-Tenant-Id → 400。"""
    c = TestClient(server.app)
    r = c.post("/v1/ingest", json={"source_path": "/tmp"})
    assert r.status_code == 400


def test_ingest_happy_path(client_with_index, tmp_path: Path):
    """空目录 → listed=0 / new=0 / failures=0。"""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    r = client_with_index.post(
        "/v1/ingest",
        json={"source_path": str(empty_dir)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme-corp"
    assert body["run_id"].startswith("ing-")
    assert body["listed"] == 0
    assert body["new"] == 0
    assert body["changed"] == 0
    assert body["failures"] == []


# ── 3. Webhook 注册 / 列表 / 删除 ──


def test_webhook_register_list_delete(tmp_webhooks_path):
    """完整 CRUD 流程。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    w = reg.register(
        tenant_id="acme-corp",
        url="https://example.com/hook",
        events=["query_completed"],
        secret="my-secret-123",
    )
    assert w.id.startswith("wh_")
    assert w.secret == "my-secret-123"

    listed = reg.list_for_tenant(tenant_id="acme-corp")
    assert len(listed) == 1
    assert listed[0].id == w.id

    ok = reg.delete(webhook_id=w.id, tenant_id="acme-corp")
    assert ok is True
    assert reg.list_for_tenant(tenant_id="acme-corp") == []


def test_webhook_cross_tenant_delete_blocked(tmp_webhooks_path):
    """A tenant 注册的 webhook 不能被 B tenant 删（防跨租户）。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    w = reg.register(
        tenant_id="acme-corp",
        url="https://example.com/hook",
        secret="my-secret-123",
    )
    ok = reg.delete(webhook_id=w.id, tenant_id="other-tenant")
    assert ok is False
    # webhook 仍在
    assert len(reg.list_for_tenant(tenant_id="acme-corp")) == 1


def test_webhook_register_invalid_url(tmp_webhooks_path):
    """非 http(s) URL 拒绝。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    with pytest.raises(ValueError, match="http"):
        reg.register(
            tenant_id="acme-corp",
            url="file:///etc/passwd",
            secret="my-secret-123",
        )


def test_webhook_register_invalid_event(tmp_webhooks_path):
    """未知事件类型拒绝。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    with pytest.raises(ValueError, match="unknown event"):
        reg.register(
            tenant_id="acme-corp",
            url="https://example.com/hook",
            events=["unknown_event"],
            secret="my-secret-123",
        )


def test_webhook_short_secret_rejected(tmp_webhooks_path):
    """secret < 8 字符拒绝。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    with pytest.raises(ValueError, match="≥ 8"):
        reg.register(
            tenant_id="acme-corp",
            url="https://example.com/hook",
            secret="short",
        )


def test_webhook_server_endpoints(client_with_index, tmp_webhooks_path):
    """通过 server 端点 CRUD webhook。"""
    r = client_with_index.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["query_completed"],
            "secret": "my-secret-abc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme-corp"
    assert "secret" not in body  # secret 不暴露
    wid = body["id"]

    # 列表
    r = client_with_index.get("/v1/webhooks")
    assert r.status_code == 200
    assert len(r.json()["webhooks"]) == 1

    # 删
    r = client_with_index.delete(f"/v1/webhooks/{wid}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # 再次删 → 404
    r = client_with_index.delete(f"/v1/webhooks/{wid}")
    assert r.status_code == 404


# ── 4. HMAC 签名校验 ──


def test_sign_payload_is_deterministic():
    """同一 secret + body → 同一 signature。"""
    from webhooks import sign_payload

    body = b'{"event":"query_completed"}'
    s1 = sign_payload("secret-123", body)
    s2 = sign_payload("secret-123", body)
    assert s1 == s2
    assert s1.startswith("sha256=")


def test_sign_payload_different_secret_changes_signature():
    from webhooks import sign_payload

    body = b"x"
    s1 = sign_payload("secret-A", body)
    s2 = sign_payload("secret-B", body)
    assert s1 != s2


def test_dispatch_async_signs_payload_correctly(tmp_webhooks_path):
    """dispatch_async 派发的请求 X-Webhook-Signature 与 HMAC 一致。"""
    from webhooks import dispatch_async, get_webhook_registry

    # mock HTTP server 收 POST
    received: list[tuple[dict, bytes, dict]] = []
    event = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append(
                (
                    dict(self.headers),
                    body,
                    {"path": self.path},
                )
            )
            self.send_response(200)
            self.end_headers()
            event.set()

        def log_message(self, *_args):  # noqa: N802
            pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        reg = get_webhook_registry()
        secret = "test-secret-xyz"
        reg.register(
            tenant_id="acme-corp",
            url=f"http://127.0.0.1:{port}/hook",
            events=["query_completed"],
            secret=secret,
        )
        n = dispatch_async(
            tenant_id="acme-corp",
            event="query_completed",
            payload={"q": "hi", "latency_ms": 12.3},
        )
        assert n == 1
        # 等异步派发（最多 5s）
        assert event.wait(timeout=5), "webhook 没在 5s 内送达"

        headers, body, _meta = received[0]
        sig_header = headers.get("X-Webhook-Signature")
        assert sig_header is not None
        # 验签：自己算一遍
        from webhooks import sign_payload

        expected = sign_payload(secret, body)
        assert sig_header == expected, f"signature mismatch: {sig_header} vs {expected}"
        # body 应含 event + tenant_id + payload
        body_json = json.loads(body)
        assert body_json["event"] == "query_completed"
        assert body_json["tenant_id"] == "acme-corp"
        assert body_json["payload"]["q"] == "hi"
    finally:
        srv.shutdown()
        srv.server_close()


def test_dispatch_async_filters_by_event(tmp_webhooks_path):
    """订阅 events=[A] 不收 events=[B]。"""
    from webhooks import dispatch_async, get_webhook_registry

    reg = get_webhook_registry()
    reg.register(
        tenant_id="acme-corp",
        url="http://127.0.0.1:1/dead",
        events=["query_completed"],  # 仅订阅 query
        secret="secret-1234",
    )
    n = dispatch_async(
        tenant_id="acme-corp",
        event="audit_written",  # 不订阅的事件
        payload={"x": 1},
    )
    assert n == 0  # 无 webhook 订阅该 event


def test_dispatch_async_returns_zero_for_unknown_event(tmp_webhooks_path):
    """未知 event → 立即返 0（不抛）。"""
    from webhooks import dispatch_async

    n = dispatch_async(
        tenant_id="acme-corp",
        event="does_not_exist",
        payload={},
    )
    assert n == 0


def test_dispatch_async_empty_event_list_means_all(tmp_webhooks_path):
    """注册时 events=[] 表示订阅所有事件。"""
    from webhooks import dispatch_async, get_webhook_registry

    reg = get_webhook_registry()
    reg.register(
        tenant_id="acme-corp",
        url="http://127.0.0.1:1/dead",
        events=[],
        secret="secret-1234",
    )
    n_query = dispatch_async(
        tenant_id="acme-corp",
        event="query_completed",
        payload={},
    )
    n_quota = dispatch_async(
        tenant_id="acme-corp",
        event="quota_exceeded",
        payload={},
    )
    assert n_query == 1
    assert n_quota == 1


# ── 5. /v1/webhooks 跨租户隔离 ──


def test_webhook_list_only_shows_own_tenant(client_with_index, tmp_webhooks_path):
    """list 只显示本 tenant 的 webhook。"""
    from webhooks import get_webhook_registry

    reg = get_webhook_registry()
    reg.register(
        tenant_id="acme-corp",
        url="https://example.com/a",
        secret="secret-aaaa",
    )
    reg.register(
        tenant_id="other-tenant",
        url="https://example.com/b",
        secret="secret-bbbb",
    )

    r = client_with_index.get("/v1/webhooks")
    assert r.status_code == 200
    hooks = r.json()["webhooks"]
    assert len(hooks) == 1
    assert hooks[0]["tenant_id"] == "acme-corp"


# ── 6. Webhook 注册响应不含 secret（防泄漏）──


def test_webhook_register_response_omits_secret(client_with_index, tmp_webhooks_path):
    """注册响应不应回显 secret（一次性生成 + 客户端自己存）。"""
    r = client_with_index.post(
        "/v1/webhooks",
        json={
            "url": "https://example.com/hook",
            "events": ["query_completed"],
            "secret": "my-secret-123",
        },
    )
    body = r.json()
    assert "secret" not in body


# ── 7. 进程级单例 ──


def test_get_webhook_registry_returns_singleton(tmp_webhooks_path):
    from webhooks import get_webhook_registry

    r1 = get_webhook_registry()
    r2 = get_webhook_registry()
    assert r1 is r2


def test_module_exports():
    """__all__ 含 webhook 模块公开符号。"""
    import webhooks as w

    expected = {
        "ALLOWED_EVENTS",
        "Webhook",
        "WebhookRegistry",
        "dispatch_async",
        "get_webhook_registry",
        "sign_payload",
    }
    assert expected.issubset(set(w.__all__))
