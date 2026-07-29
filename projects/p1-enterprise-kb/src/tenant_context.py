"""P1 — Tenant 上下文变量（ENTERPRISE_AUDIT.md S2）。

为什么用 ContextVar（不是 thread-local / 全局变量）：
- FastAPI 异步 handler 在同一线程上跑多个请求（context switch 切协程），
  必须用 ContextVar 才能让每个请求的 tenant 互不污染。
- 与现有 `observability.trace_id_var` 同一套机制，metrics / 日志自动带 tenant 字段。

约定：
- **任何路径下都不允许 None tenant 走到 vector store**。这是隔离的核心。
  fetch_for_user / retrieval 都应通过 require_current_tenant() 拿到非空 Tenant。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from types import TracebackType

from tenant import Tenant

# 默认 None —— 表示"没有 tenant context"。
# 显式调用 require_current_tenant() 时 None 会 raise，避免误走到无 tenant 的查询。
_current_tenant: ContextVar[Tenant | None] = ContextVar("current_tenant", default=None)


class TenantRequiredError(RuntimeError):
    """当前 context 没有 tenant；任何要求隔离的调用都必须先 set_tenant。"""


def get_current_tenant() -> Tenant | None:
    """读当前 tenant；可能为 None（取决于调用方是否进过 context）。"""
    return _current_tenant.get()


def require_current_tenant() -> Tenant:
    """读当前 tenant；为 None 则 raise TenantRequiredError。

    用法：在 vector store / retrieval / quota 计费前调用。
    """
    t = _current_tenant.get()
    if t is None:
        raise TenantRequiredError(
            "no tenant in current context — call set_current_tenant() "
            "or pipeline.ask(tenant_id=...) first"
        )
    return t


def set_current_tenant(tenant: Tenant) -> Token[Tenant | None]:
    """设置当前 tenant；返回 token 用于 reset。

    同时把 tenant_id 绑到 structlog contextvars（让 JSON 日志自动带 tenant 字段）。
    """
    try:
        import structlog

        structlog.contextvars.bind_contextvars(tenant_id=tenant.tenant_id)
    except ImportError:
        pass
    return _current_tenant.set(tenant)


def reset_current_tenant(token: Token[Tenant | None]) -> None:
    """解除 tenant 绑定（与 set_current_tenant 配对）。

    注意：structlog 的 unbind 只在知道字段名时调用 —— 这里直接用 .clear() 更稳。
    """
    try:
        import structlog

        structlog.contextvars.unbind_contextvars("tenant_id")
    except (ImportError, KeyError):
        pass
    _current_tenant.reset(token)


class tenant_scope:  # noqa: N801 — 小写 by design：与 `tenant_scope(t)` 用法一致
    """`with tenant_scope(t): ...` 语法糖 —— 比手动 set/reset 更安全。

    用法::

        with tenant_scope(tenant):
            result = pipeline.ask(...)  # 内部自动读到 tenant
    """

    def __init__(self, tenant: Tenant) -> None:
        self.tenant = tenant
        self._token: Token[Tenant | None] | None = None

    def __enter__(self) -> Tenant:
        self._token = set_current_tenant(self.tenant)
        return self.tenant

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            reset_current_tenant(self._token)
            self._token = None


__all__ = [
    "TenantRequiredError",
    "get_current_tenant",
    "require_current_tenant",
    "set_current_tenant",
    "reset_current_tenant",
    "tenant_scope",
]
