"""P1 ACL：三档敏感度 + 用户角色 → 可见级别。

path ACL：自实现（详见 `_compat.is_path_allowed` 与 ADR-0005）。
文档敏感度过滤：根据用户角色过滤文档 sensitivity。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Sensitivity(int, Enum):
    PUBLIC = 1  # 所有人可见
    INTERNAL = 2  # 内部员工可见
    RESTRICTED = 3  # 仅特定角色可见


# 角色 → 可见级别（数值越大越严格）
ROLE_SENSITIVITY: dict[str, int] = {
    "intern": Sensitivity.PUBLIC,
    "employee": Sensitivity.INTERNAL,
    "manager": Sensitivity.INTERNAL,
    "executive": Sensitivity.RESTRICTED,
    "admin": Sensitivity.RESTRICTED,
}


_SENSITIVITY_ALIASES = {"public": 1, "internal": 2, "restricted": 3, "": 1}


@dataclass
class User:
    user_id: str
    role: str  # intern/employee/manager/executive/admin

    def can_see(self, sensitivity: str) -> bool:
        """根据角色判断是否可见该敏感度文档。"""
        user_level = ROLE_SENSITIVITY.get(self.role, 1)
        doc_level = _SENSITIVITY_ALIASES.get(sensitivity, 1)
        return user_level >= doc_level  # 简化：值 >= 即可见


def filter_docs(
    docs: Iterable[dict],
    user: User,
) -> list[dict]:
    """按用户角色过滤 docs（每条 doc 有 'sensitivity' 字段）。"""
    return [d for d in docs if user.can_see(d.get("sensitivity", "public"))]


def is_path_allowed(path: str | Path) -> bool:
    """路径 ACL（代理到 `_compat.is_path_allowed`，详见 ADR-0005）。

    - 兜底拒绝（.env/.ssh/secrets/etc/..）
    - 可选 `P1_VAULT_ALLOWLIST` 启用严格模式：路径必须落在任一允许前缀下
    """
    from _compat import is_path_allowed as _impl

    return _impl(path)
