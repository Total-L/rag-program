"""P1 Pipeline 单测：可索引 + 问答案。"""

from acl import User
from pipeline import P1Pipeline  # noqa: E402


def test_pipeline_constructs():
    """不需真实数据也能构造（idx 为空时 ask 返回拒答）。"""
    p = P1Pipeline(user=User("u", "manager"))
    assert p.user.role == "manager"
    assert p.index == []


def test_ask_empty_index_abstains():
    p = P1Pipeline(user=User("u", "manager"))
    r = p.ask("员工每年能休多少天年假？", tenant_id="acme-corp")
    assert r.abstained or "无索引" in r.answer
    assert r.user_role == "manager"
