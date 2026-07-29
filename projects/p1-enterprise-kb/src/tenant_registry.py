"""P1 — Tenant 注册表（ENTERPRISE_AUDIT.md S2）。

dev/MVP 实现：从静态 JSON 文件加载。
生产可换 DB / KV，**只要满足 TenantRegistry Protocol** 即可。

JSON 格式 (tenants.json)：

    {
      "acme-corp": {
        "name": "Acme Corp",
        "plan": "team",
        "api_key": "sk-acme-xxxxxxxx",
        "custom_quota": null
      },
      "startup": {
        "name": "Startup Inc",
        "plan": "free"
      }
    }

字段说明：
- `name`：人类可读名（用于日志 / audit）
- `plan`：free / team / enterprise（决定 quota 默认值）
- `api_key`：该租户绑定的 API key；空串 = 不绑（用 server 全局 API_KEY）
- `custom_quota` (可选)：覆盖 plan 默认 quota；null = 用 plan 默认
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from tenant import DEFAULT_QUOTAS, Plan, Quota, Tenant, TenantNotFoundError

DEFAULT_TENANTS_PATH = Path(
    os.environ.get(
        "RAG_PROG_TENANTS_PATH",
        str(Path(__file__).resolve().parents[1] / "config" / "tenants.json"),
    )
)


class TenantRegistry(Protocol):
    """注册表接口：MVP 静态实现 + 生产 DB 实现都满足这个协议。"""

    def get(self, tenant_id: str) -> Tenant: ...
    def list_ids(self) -> list[str]: ...


class StaticTenantRegistry:
    """从 JSON 文件加载的注册表。线程安全（dict 读 + GIL）。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_TENANTS_PATH
        self._tenants: dict[str, Tenant] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            # 首次启动：空注册表不算 bug，但要让调用方知情
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"failed to parse tenants.json at {self.path}: {e}") from e
        for tid, spec in data.items():
            plan_str = spec.get("plan", "free")
            try:
                plan = Plan(plan_str)
            except ValueError as e:
                raise RuntimeError(
                    f"tenant {tid!r}: invalid plan {plan_str!r} (must be free|team|enterprise)"
                ) from e
            custom = spec.get("custom_quota")
            if custom:
                quota = Quota(
                    max_queries_per_day=int(
                        custom.get("max_queries_per_day", DEFAULT_QUOTAS[plan].max_queries_per_day)
                    ),
                    max_tokens_per_day=int(
                        custom.get("max_tokens_per_day", DEFAULT_QUOTAS[plan].max_tokens_per_day)
                    ),
                    max_chunks_indexed=int(
                        custom.get("max_chunks_indexed", DEFAULT_QUOTAS[plan].max_chunks_indexed)
                    ),
                )
            else:
                quota = DEFAULT_QUOTAS[plan]
            self._tenants[tid] = Tenant(
                tenant_id=tid,
                name=spec.get("name", tid),
                quota=quota,
                api_key=spec.get("api_key", ""),
            )

    def reload(self) -> None:
        """重新从文件加载（用于运行时添加租户后刷新）。"""
        self._tenants.clear()
        self._load()

    def get(self, tenant_id: str) -> Tenant:
        if tenant_id not in self._tenants:
            raise TenantNotFoundError(f"tenant {tenant_id!r} not registered")
        return self._tenants[tenant_id]

    def list_ids(self) -> list[str]:
        return sorted(self._tenants.keys())

    def __contains__(self, tenant_id: object) -> bool:
        return isinstance(tenant_id, str) and tenant_id in self._tenants


# ── 进程级单例（与 observability 同一惯例）──

_registry: TenantRegistry | None = None


def get_registry() -> TenantRegistry:
    """获取（或懒初始化）全局注册表。"""
    global _registry
    if _registry is None:
        _registry = StaticTenantRegistry()
    return _registry


def set_registry(reg: TenantRegistry) -> None:
    """测试用：注入自定义 registry（如内存 mock）。"""
    global _registry
    _registry = reg


def reset_registry() -> None:
    """测试 teardown。"""
    global _registry
    _registry = None


__all__ = [
    "TenantRegistry",
    "StaticTenantRegistry",
    "DEFAULT_TENANTS_PATH",
    "get_registry",
    "set_registry",
    "reset_registry",
]
