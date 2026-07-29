"""P1 ACL 单测：三档敏感度 + 角色过滤。"""

from acl import User, filter_docs, is_path_allowed  # noqa: E402


def test_role_can_see_public():
    u = User("alice", "intern")
    assert u.can_see("public")
    assert not u.can_see("internal")
    assert not u.can_see("restricted")


def test_role_can_see_internal():
    u = User("bob", "manager")
    assert u.can_see("public")
    assert u.can_see("internal")
    assert not u.can_see("restricted")


def test_role_can_see_restricted():
    u = User("ceo", "executive")
    assert u.can_see("public")
    assert u.can_see("internal")
    assert u.can_see("restricted")


def test_filter_docs():
    docs = [
        {"doc_id": "d1", "sensitivity": "public", "text": "..."},
        {"doc_id": "d2", "sensitivity": "internal", "text": "..."},
        {"doc_id": "d3", "sensitivity": "restricted", "text": "..."},
    ]
    intern = User("u", "intern")
    assert len(filter_docs(docs, intern)) == 1
    manager = User("u", "manager")
    assert len(filter_docs(docs, manager)) == 2
    exec_ = User("u", "executive")
    assert len(filter_docs(docs, exec_)) == 3


def test_path_allowed():
    assert not is_path_allowed("/tmp/secrets/key.txt")
    assert not is_path_allowed("/Users/x/.env")
    assert is_path_allowed("/Users/x/notes/HR/year.md")
