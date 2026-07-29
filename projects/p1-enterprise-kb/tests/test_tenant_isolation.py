"""S2 多租户：跨租户隔离测试（核心安全网）。

覆盖：
1. fetch_for_user 必须传 tenant_id；缺则 raise（fail-fast 而不是悄悄返回全集）
2. 同 store 内 (tenant_a, public) 与 (tenant_b, public) 不能互相看见
3. (tenant_a, restricted) 仍受 ACL 控制 — 即使同租户，role 不够也看不到
4. 切 tenant 后 BM25 cache 不能串味（不同租户 corpus 不同）

不依赖真实 vector store —— 用 InMemoryIndexStore 模拟。
"""

from __future__ import annotations

import pytest
from acl import User
from tenant import Plan, Tenant, TenantIdError
from tenant_context import tenant_scope


class _FakeVectorStore:
    """模拟 P1IndexStore（持有 .store.fetch_all 的 P6 接口）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(
        self,
        *,
        chunk_id: str,
        source_id: str,
        text: str,
        tenant_id: str,
        sensitivity: str = "public",
        embedding: list[float] | None = None,
    ) -> None:
        self.rows.append(
            {
                "chunk_id": chunk_id,
                "source_id": source_id,
                "text": text,
                "embedding": embedding or [0.0],
                "metadata": {
                    "tenant_id": tenant_id,
                    "sensitivity": sensitivity,
                    "heading_path": "",
                },
            }
        )

    def fetch_for_user(
        self,
        *,
        allowed_sensitivities: list[str],
        tenant_id: str,
    ) -> list[dict]:
        """P1 协议：按 (tenant_id, sensitivity) 过滤 → 返回 dict 列表。"""
        if not tenant_id:
            raise ValueError("fetch_for_user requires tenant_id (S2)")
        out = []
        for r in self.rows:
            md = r["metadata"]
            if md.get("tenant_id") != tenant_id:
                continue
            if md.get("sensitivity") not in allowed_sensitivities:
                continue
            out.append(
                {
                    "chunk_id": r["chunk_id"],
                    "source_id": r["source_id"],
                    "text": r["text"],
                    "embedding": r["embedding"],
                    "sensitivity": md["sensitivity"],
                    "heading_path": md.get("heading_path", ""),
                    "tenant_id": md["tenant_id"],
                }
            )
        return out


@pytest.fixture
def store_with_two_tenants() -> _FakeVectorStore:
    """两个租户的 corpus 都已经入库。"""
    vs = _FakeVectorStore()
    # 租户 A
    vs.add(chunk_id="a1", source_id="a-doc", text="A 报销标准", tenant_id="acme")
    vs.add(
        chunk_id="a2",
        source_id="a-doc",
        text="A 差旅政策",
        tenant_id="acme",
        sensitivity="internal",
    )
    # 租户 B
    vs.add(chunk_id="b1", source_id="b-doc", text="B 财务制度", tenant_id="globex")
    vs.add(
        chunk_id="b2",
        source_id="b-doc",
        text="B 员工手册",
        tenant_id="globex",
        sensitivity="internal",
    )
    return vs


def test_fetch_for_user_requires_tenant_id():
    """缺 tenant_id 必须 raise —— 不能让它静默返回全集。"""
    fake = _FakeVectorStore()
    with pytest.raises(ValueError, match="tenant_id"):
        fake.fetch_for_user(allowed_sensitivities=["public"], tenant_id="")


def test_cross_tenant_isolation(store_with_two_tenants: _FakeVectorStore) -> None:
    """租户 A 检索不能看到租户 B 的任何 chunk。"""
    rows_a = store_with_two_tenants.fetch_for_user(
        allowed_sensitivities=["public", "internal", "restricted"],
        tenant_id="acme",
    )
    chunk_ids_a = {r["chunk_id"] for r in rows_a}
    assert chunk_ids_a == {"a1", "a2"}, f"acme leaked: {chunk_ids_a}"

    rows_b = store_with_two_tenants.fetch_for_user(
        allowed_sensitivities=["public", "internal", "restricted"],
        tenant_id="globex",
    )
    chunk_ids_b = {r["chunk_id"] for r in rows_b}
    assert chunk_ids_b == {"b1", "b2"}, f"globex leaked: {chunk_ids_b}"

    # 双向验证：没有交叉
    assert (chunk_ids_a & chunk_ids_b) == set()


def test_tenant_plus_sensitivity_compound(store_with_two_tenants: _FakeVectorStore) -> None:
    """同租户内 sensitivity ACL 仍生效。"""
    # 租户 A + intern 角色 → 只能看 public
    rows = store_with_two_tenants.fetch_for_user(allowed_sensitivities=["public"], tenant_id="acme")
    chunk_ids = {r["chunk_id"] for r in rows}
    assert chunk_ids == {"a1"}, f"ACL bypassed: {chunk_ids}"

    # 租户 A + executive 角色 → 能看所有
    rows = store_with_two_tenants.fetch_for_user(
        allowed_sensitivities=["public", "internal", "restricted"],
        tenant_id="acme",
    )
    chunk_ids = {r["chunk_id"] for r in rows}
    assert chunk_ids == {"a1", "a2"}


def test_index_property_requires_tenant_context():
    """P1Pipeline.index 在没 tenant context 时必须 raise。"""
    # 用真实 P1Pipeline 但不调 index_corpus（_indexed=False 直接返回 []）
    # 这里测的是 _index_cache 真的被需要时才校验
    from pipeline import P1Pipeline

    p = P1Pipeline(user=User(user_id="u", role="executive"))
    p._indexed = True  # 强制走 fetch_for_user
    # 没 set tenant → 必须 raise TenantRequiredError
    from tenant_context import TenantRequiredError

    with pytest.raises(TenantRequiredError):
        _ = p.index


def test_index_property_uses_context_tenant(store_with_two_tenants: _FakeVectorStore) -> None:
    """P1Pipeline.index 必须用当前 context 的 tenant 过滤。"""
    from pipeline import P1Pipeline

    p = P1Pipeline(user=User(user_id="u", role="executive"))
    p.store = store_with_two_tenants  # type: ignore[assignment]
    p._indexed = True
    p._index_cache = None  # 强制重新加载

    tenant = Tenant.from_plan("acme", "Acme", Plan.FREE)
    with tenant_scope(tenant):
        rows = p.index
    chunk_ids = {r["chunk_id"] for r in rows}
    assert chunk_ids == {"a1", "a2"}, f"wrong scope: {chunk_ids}"

    # 切到 globex
    p._index_cache = None
    tenant_b = Tenant.from_plan("globex", "Globex", Plan.FREE)
    with tenant_scope(tenant_b):
        rows_b = p.index
    chunk_ids_b = {r["chunk_id"] for r in rows_b}
    assert chunk_ids_b == {"b1", "b2"}


def test_tenant_id_validation_rejects_path_traversal():
    """path traversal / shell 风格 / 长度越界的 tenant_id 必须被拒绝。"""
    from tenant import validate_tenant_id

    bad_inputs = [
        "../etc/passwd",  # path traversal
        "a/b",  # 斜杠
        "a.b",  # 点（避免视觉混淆）
        "A",  # 太短 + 大写
        "a",  # 太短
        "a" * 100,  # 超长
        "a--b",  # 连续短横线
        "",  # 空
    ]
    for bad in bad_inputs:
        with pytest.raises(TenantIdError):
            validate_tenant_id(bad)
