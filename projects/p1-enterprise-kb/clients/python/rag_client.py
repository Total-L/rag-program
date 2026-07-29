"""P1 RAG Python SDK（ENTERPRISE_AUDIT.md M2b）。

薄壳 client：包装 /v1/query 端点。用法：

    from rag_client import RagClient

    client = RagClient(base_url="http://localhost:8080", api_key="secret")
    resp = client.query("员工年假？")
    print(resp.answer, resp.citations, resp.trace_id)

测试：
    pytest tests/test_sdk.py -v

错误处理：
- httpx.HTTPStatusError → RagAPIError（含 status code + body）
- httpx.RequestError → RagConnectionError（连接失败 / 超时）
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx


@dataclass
class Citation:
    id: str
    text: str | None = None
    chunk_id: str | None = None


@dataclass
class QueryResult:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    abstained: bool = False
    user_role: str = ""
    latency_ms: float = 0.0
    trace_id: str | None = None
    retrieved_source_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, body: dict) -> QueryResult:
        return cls(
            question=body["question"],
            answer=body["answer"],
            citations=[Citation(**c) for c in body.get("citations", [])],
            abstained=body.get("abstained", False),
            user_role=body.get("user_role", ""),
            latency_ms=body.get("latency_ms", 0.0),
            trace_id=body.get("trace_id"),
            retrieved_source_ids=body.get("retrieved_source_ids", []),
            retrieved_chunk_ids=body.get("retrieved_chunk_ids", []),
        )


class RagAPIError(Exception):
    """HTTP error from RAG API."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"RAG API {status_code}: {detail}")


class RagConnectionError(Exception):
    """Connection failed / timeout."""


class RagClient:
    """Thin wrapper around /v1/query.

    Args:
        base_url: server URL（如 "http://localhost:8080"）
        api_key: X-API-Key（服务端需配 RAG_PROG_API_KEY 才生效）
        tenant_id: X-Tenant-Id（S2 多租户；不传则每次 query 必须传）
        timeout: 请求超时（秒）
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: str | None = None,
        tenant_id: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.timeout = timeout

    def _headers(
        self,
        trace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if trace_id:
            h["X-Trace-Id"] = trace_id  # 预留扩展
        # 优先用 query() 显式传的 tenant_id；否则用 client 构造时的
        tid = tenant_id or self.tenant_id
        if tid:
            h["X-Tenant-Id"] = tid
        return h

    def query(
        self,
        question: str,
        *,
        user_id: str = "anonymous",
        user_role: str | None = None,
        trace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> QueryResult:
        """同步 RAG 查询。

        Args:
            question: 用户问题
            user_id: 用户 ID
            user_role: 用户角色（不传则用 server 默认）
            trace_id: 外部 trace ID
            tenant_id: 覆盖 client 默认 tenant_id（每次请求级）

        Returns:
            QueryResult

        Raises:
            RagAPIError: HTTP 4xx/5xx
            RagConnectionError: 连接失败 / 超时
        """
        payload = {
            "question": question,
            "user_id": user_id,
        }
        if user_role:
            payload["user_role"] = user_role
        if trace_id:
            payload["trace_id"] = trace_id

        try:
            resp = httpx.post(
                f"{self.base_url}/v1/query",
                json=payload,
                headers=self._headers(trace_id, tenant_id),
                timeout=self.timeout,
            )
        except httpx.RequestError as e:
            raise RagConnectionError(f"connection failed: {e}") from e

        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise RagAPIError(resp.status_code, str(detail))

        return QueryResult.from_api(resp.json())

    def healthz(self) -> bool:
        """检查 server liveness。"""
        try:
            r = httpx.get(f"{self.base_url}/v1/healthz", timeout=self.timeout)
            return r.status_code == 200
        except httpx.RequestError:
            return False

    def readyz(self) -> tuple[bool, str]:
        """检查 server readiness。

        Returns:
            (ready, reason) — reason 是 server 返回的 status / reason
        """
        try:
            r = httpx.get(f"{self.base_url}/v1/readyz", timeout=self.timeout)
            body = r.json()
            return body.get("status") == "ready", body.get("reason", body.get("status", ""))
        except httpx.RequestError as e:
            return False, f"connection failed: {e}"


# ── CLI 演示 ──


def _demo():
    """CLI 演示：健康检查 + 一次 query。"""
    import os

    client = RagClient(
        base_url=os.environ.get("RAG_API_URL", "http://localhost:8080"),
        api_key=os.environ.get("RAG_API_KEY"),
    )
    print(f"→ healthz: {client.healthz()}")
    ready, reason = client.readyz()
    print(f"→ readyz: {ready} ({reason})")
    if ready:
        result = client.query("员工年假？")
        print(f"→ answer: {result.answer[:100]}")
        print(f"→ citations: {len(result.citations)}")
        print(f"→ trace_id: {result.trace_id}")


if __name__ == "__main__":
    _demo()
