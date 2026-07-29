"""P1 — 多租户数据模型（ENTERPRISE_AUDIT.md S2 维度 8）。

设计取舍：
- **logical 隔离**（where filter），不做物理隔离。理由：现有 ACL 已用 WHERE
  pushdown，迁移成本最低；schema-per-tenant 是 SaaS 大客户阶段才该考虑的事。
- **tenant_id 格式** `^[a-z0-9-]{3,32}$`：slug 风格，URL/header 友好可读。
- **plan 与 quota 强绑定**：改 plan → 改 quota。避免运行时漂移。
- 不在此处引入 ORM / DB —— dev/MVP 用静态 JSON（tenant_registry.py），
  接口稳定，后续换 DB 不破坏消费方。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# ── tenant_id 格式约束 ──
# 字母数字短横线，3-32 字符。避免 shell 注入风险字符（空格 / 点 / 斜杠 / @ 等）。
_TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


class TenantIdError(ValueError):
    """tenant_id 格式不合法。"""


class Plan(StrEnum):
    """租户套餐。决定默认 Quota 上限。"""

    FREE = "free"
    TEAM = "team"
    ENTERPRISE = "enterprise"


# ── Quota 数据类 ──


@dataclass(frozen=True)
class Quota:
    """租户配额上限。"""

    max_queries_per_day: int = 500
    max_tokens_per_day: int = 200_000
    max_chunks_indexed: int = 10_000

    def __post_init__(self) -> None:
        if self.max_queries_per_day < 0:
            raise ValueError("max_queries_per_day must be >= 0")
        if self.max_tokens_per_day < 0:
            raise ValueError("max_tokens_per_day must be >= 0")
        if self.max_chunks_indexed < 0:
            raise ValueError("max_chunks_indexed must be >= 0")


# ── 套餐默认配额 ──
# 数字经过估算：FREE=小团队试水；TEAM=中型 SaaS；ENTERPRISE=大客户。
# 后续可改 env 注入，覆盖默认值。
DEFAULT_QUOTAS: dict[Plan, Quota] = {
    Plan.FREE: Quota(
        max_queries_per_day=500,
        max_tokens_per_day=200_000,
        max_chunks_indexed=10_000,
    ),
    Plan.TEAM: Quota(
        max_queries_per_day=5_000,
        max_tokens_per_day=2_000_000,
        max_chunks_indexed=100_000,
    ),
    Plan.ENTERPRISE: Quota(
        max_queries_per_day=50_000,
        max_tokens_per_day=20_000_000,
        max_chunks_indexed=1_000_000,
    ),
}


def validate_tenant_id(value: str) -> str:
    """校验 tenant_id 格式；不合法则 raise TenantIdError。

    校验规则：
    - 必须匹配 `^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$`（总长 3-32）
    - 不能连续两个短横线（防 `--` 注入或视觉歧义）
    """
    if not isinstance(value, str):
        raise TenantIdError(f"tenant_id must be str, got {type(value).__name__}")
    if not _TENANT_ID_RE.match(value):
        raise TenantIdError(
            f"invalid tenant_id {value!r}: must match [a-z0-9][a-z0-9-]{{1,30}}[a-z0-9]"
        )
    if "--" in value:
        raise TenantIdError(f"invalid tenant_id {value!r}: consecutive hyphens not allowed")
    return value


@dataclass(frozen=True)
class Tenant:
    """一个租户的不变量 + 元数据。

    注意：**Tenant 一旦创建就是 immutable**。改 plan / 改 quota 必须新建实例。
    这避免了"配额计算时快照不一致"的并发 bug。

    设计：Tenant 只持有 `quota`（不动 plan 字段）；plan 只在 from_plan() 工厂方法里
    决定 quota 的初值，避免 "default_factory 不会读 plan 参数" 的陷阱。
    """

    tenant_id: str  # 校验过的合法 slug
    name: str
    quota: Quota
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    api_key: str = ""  # 绑定的 API key；空串 = 用 server 全局 API_KEY（dev 模式）

    def __post_init__(self) -> None:
        # 校验 tenant_id —— 冻结前必须做完
        object.__setattr__(self, "tenant_id", validate_tenant_id(self.tenant_id))

    @classmethod
    def from_plan(
        cls,
        tenant_id: str,
        name: str,
        plan: Plan = Plan.FREE,
        *,
        api_key: str = "",
    ) -> Tenant:
        """便捷构造：根据 plan 选默认 quota。

        需要自定义 quota 时直接 Tenant(tenant_id, name, Quota(...))。
        """
        return cls(
            tenant_id=tenant_id,
            name=name,
            quota=DEFAULT_QUOTAS[plan],
            api_key=api_key,
        )


class TenantNotFoundError(LookupError):
    """TenantRegistry.get(tid) 找不到。"""


class QuotaExceeded(Exception):  # noqa: N818 — "Error" 后缀在 Python 不强制；语义清晰更重要
    """配额超限。携带 (tenant_id, metric, limit, current) 用于上游决策。"""

    def __init__(
        self,
        *,
        tenant_id: str,
        metric: str,  # "queries" / "tokens" / "chunks"
        limit: int,
        current: int,
    ) -> None:
        self.tenant_id = tenant_id
        self.metric = metric
        self.limit = limit
        self.current = current
        super().__init__(
            f"quota exceeded for tenant {tenant_id!r}: {metric}={current} > limit={limit}"
        )


__all__ = [
    "Plan",
    "Quota",
    "Tenant",
    "TenantIdError",
    "TenantNotFoundError",
    "QuotaExceeded",
    "DEFAULT_QUOTAS",
    "validate_tenant_id",
]
