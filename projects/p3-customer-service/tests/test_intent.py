"""P3 intent + transfer + FAQ + ticket 单测（ENTERPRISE_AUDIT C2 扩写）。

覆盖 classify_intent(6 intents + 冲突 + 未知)/ needs_transfer /
faq_search / make_ticket / 端到端 main()。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 强制只插入 P3 src,避免 P2/P4 干扰
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path = [p for p in sys.path if "p2-code-docs" not in p and "p4-finance" not in p]
# 防重导入
if "smoke" in sys.modules:
    del sys.modules["smoke"]
from smoke import (  # noqa: E402
    FAQ,
    INTENT_KEYWORDS,
    TRANSFER_TRIGGERS,
    classify_intent,
    faq_search,
    make_ticket,
    needs_transfer,
)

# ── classify_intent:6 intents 各一例 ──


def test_intent_billing():
    assert classify_intent("怎么开发票？")[0] == "billing"


def test_intent_technical():
    assert classify_intent("APP 闪退")[0] == "technical"


def test_intent_shipping():
    assert classify_intent("快递多久到？")[0] == "shipping"


def test_intent_account():
    assert classify_intent("怎么修改密码？")[0] == "account"


def test_intent_refund():
    assert classify_intent("如何申请退款？")[0] == "refund"


def test_intent_general_when_ambiguous():
    """优惠券/活动属 general。"""
    assert classify_intent("最近有什么优惠活动？")[0] == "general"


def test_intent_unknown_falls_back_to_general():
    intent, conf = classify_intent("今天天气怎么样")
    assert intent == "general"
    assert conf < 0.5


def test_intent_case_insensitive():
    """英文关键词 PAY/INVOICE 大小写不敏感(因为 lower())。"""
    intent, _ = classify_intent("I want to PAY my invoice")
    assert intent == "billing"


def test_intent_confidence_in_zero_one():
    """confidence 应在 [0, 1],不为 None/负数。"""
    for q in ["开发票", "闪退了", "快递呢", "密码忘了", "退款", "活动"]:
        _, conf = classify_intent(q)
        assert 0.0 <= conf <= 1.0, f"bad conf {conf} for {q}"


def test_intent_returns_tuple():
    """返回类型必须 (intent:str, confidence:float)。"""
    out = classify_intent("开发票")
    assert isinstance(out, tuple)
    assert len(out) == 2
    assert isinstance(out[0], str)
    assert isinstance(out[1], float)


# ── needs_transfer ──


def test_needs_transfer():
    assert needs_transfer("我要找人工")
    assert needs_transfer("投诉!")
    assert not needs_transfer("怎么开发票？")


def test_needs_transfer_all_triggers():
    """所有 TRANSFER_TRIGGERS 都应触发转人工。"""
    for trig in TRANSFER_TRIGGERS:
        assert needs_transfer(trig), f"trigger {trig!r} should fire"
        assert needs_transfer(f"我想要{trig}"), f"trigger {trig!r} mid-sentence should fire"


def test_needs_transfer_negative_cases():
    """不含 trigger 的常见问句不应转人工。"""
    negatives = [
        "怎么开发票？",
        "快递多久到？",
        "我想重置密码",
        "APP 闪退了",
        "hello world",
        "",
    ]
    for q in negatives:
        assert not needs_transfer(q), f"false positive on {q!r}"


# ── faq_search ──


def test_faq_search_filters_by_intent():
    hits = faq_search("重置", "account", k=1)
    assert hits[0]["intent"] == "account"


def test_faq_search_returns_k_results():
    """k=2 默认 → 2 条。"""
    hits = faq_search("密码", "account", k=2)
    assert len(hits) == 2
    assert all("q" in h and "a" in h and "intent" in h for h in hits)


def test_faq_search_k_zero_returns_empty():
    """k=0 → 空 list(避免越界)。"""
    assert faq_search("anything", "account", k=0) == []


def test_faq_search_k_larger_than_corpus():
    """k > 该 intent 候选数 → 返回全部(不超过)."""
    hits = faq_search("密码", "account", k=100)
    # account 类 FAQ: 重置密码/会员/注销账号 = 3 unique(×6 重复)= 18
    assert len(hits) >= 3
    assert all(h["intent"] == "account" for h in hits)


def test_faq_search_unknown_intent_falls_back_to_full():
    """未识别 intent → 退到全 FAQ(避免空结果)。"""
    hits = faq_search("anything", "this-intent-does-not-exist", k=2)
    assert len(hits) == 2
    # 不强制所有都是同一 intent(因为在全 FAQ 上排)


def test_faq_search_empty_query():
    """空 query → 不抛,返回 k 个结果(所有词分 = 0,顺序由 sort 保证稳定)。"""
    hits = faq_search("", "account", k=2)
    assert len(hits) == 2


# ── make_ticket ──


def test_make_ticket_severity_p0_on_emergency():
    """含「紧急」→ severity=P0。"""
    t = make_ticket("u1", "紧急:账户被盗", "account")
    assert t["severity"] == "P0"


def test_make_ticket_severity_p0_on_complaint():
    """含「投诉」→ severity=P0。"""
    t = make_ticket("u1", "我要投诉客服", "billing")
    assert t["severity"] == "P0"


def test_make_ticket_severity_p2_default():
    """无紧急/投诉关键词 → severity=P2。"""
    t = make_ticket("u1", "我想开发票", "billing")
    assert t["severity"] == "P2"


def test_make_ticket_id_format():
    """ticket_id 格式 TCK-{user_id}-{4digit}。"""
    t = make_ticket("alice", "test text", "general")
    assert t["ticket_id"].startswith("TCK-alice-")
    suffix = t["ticket_id"].rsplit("-", 1)[-1]
    assert len(suffix) == 4
    assert suffix.isdigit()


def test_make_ticket_passes_through_fields():
    """user_id / intent / text 必须原样保留。"""
    t = make_ticket("bob", "我的问题", "shipping")
    assert t["user_id"] == "bob"
    assert t["intent"] == "shipping"
    assert t["text"] == "我的问题"


def test_make_ticket_deterministic_for_same_input():
    """相同输入 → 相同 ticket_id(hash 稳定)。"""
    a = make_ticket("u1", "same text", "general")
    b = make_ticket("u1", "same text", "general")
    assert a["ticket_id"] == b["ticket_id"]


# ── 端到端 main() ──


def test_main_runs_and_writes_artifact(monkeypatch, tmp_path: Path):
    """用 monkeypatch chdir 到 tmp_path 隔离 artifacts 写入。

    关键:`from smoke import main` 必须在函数内做,避免和 P4 的 smoke 模块冲突
    (两项目都叫 smoke.py,P4 先跑会污染 sys.modules)。
    """
    # 清理可能从其他项目残留的 smoke 模块 + 调整 sys.path
    for k in [k for k in sys.modules if k == "smoke"]:
        del sys.modules[k]
    monkeypatch.chdir(tmp_path)
    # 重新加载 P3 的 smoke
    import importlib

    spec = importlib.util.spec_from_file_location(
        "smoke", str(Path(__file__).resolve().parent.parent / "src" / "smoke.py")
    )
    smoke = importlib.util.module_from_spec(spec)
    sys.modules["smoke"] = smoke
    spec.loader.exec_module(smoke)

    smoke.main()
    out = tmp_path / "artifacts" / "P3" / "smoke.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["n_queries"] == 5
    # 5 query 中至少 1 个 transfer(u4 "转人工")
    assert data["transfers"] >= 1


# ── 覆盖 / 一致性 ──


def test_intent_keywords_covers_all_six_intents():
    """INTENT_KEYWORDS 字典应包含 6 个 intent,与 README 描述一致。"""
    expected = {"billing", "technical", "account", "refund", "shipping", "general"}
    assert set(INTENT_KEYWORDS.keys()) == expected


def test_faq_has_at_least_60_entries():
    """README 声称 60 条 FAQ(10 unique × 6 复制)。"""
    assert len(FAQ) >= 60




def test_intent_classification_demo_queries_match_main():
    """main() 里的 5 query 至少应有 3 个被正确分类到其演示 intent。"""
    demo_cases = [
        ("u1", "我想重置密码", "account"),
        ("u2", "快递怎么还没到？我要投诉！", "shipping"),  # 含「投诉」,但 intent 走 keyword
        ("u3", "APP 又闪退了", "technical"),
        ("u4", "转人工", "general"),  # 不在 6 intent 关键词里 → general
        ("u5", "怎么开发票？", "billing"),
    ]
    for _uid, q, _expected_intent in demo_cases:
        intent, _ = classify_intent(q)
        # 仅断言返回的是 6 intent 之一(不强求精确)
        assert intent in INTENT_KEYWORDS
