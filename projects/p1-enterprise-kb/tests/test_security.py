"""P1 Security 单测：复用 kbchat + 自有 ACL 双重防御。"""

from acl import User, is_path_allowed  # noqa: E402


def test_path_traversal_blocked():
    assert not is_path_allowed("/etc/passwd")
    assert not is_path_allowed("../../../etc/passwd")
    assert not is_path_allowed("~/.ssh/id_rsa")


def test_safe_paths():
    assert is_path_allowed("datasets/fixtures/enterprise/hr-001.md")
    assert is_path_allowed("notes/HR/政策.md")


def test_user_role_safety():
    """intern 不应能见 restricted。"""
    u = User("u", "intern")
    assert not u.can_see("restricted")
    # 即使绕过 ACL，restricted 文档在 filter_docs 时也必须被过滤
    docs = [{"doc_id": "secret", "sensitivity": "restricted", "text": "salary"}]
    from acl import filter_docs

    assert len(filter_docs(docs, u)) == 0
