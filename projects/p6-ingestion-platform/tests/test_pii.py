"""P6 PII 脱敏单测 —— 审计判定 critical 的缺口的回归测试。

核心断言是**泄漏率 = 0**：明文 PII 绝不能出现在入库文本或 trace 里。
这个测试就是 eval/regression 里 "pii_leak_rate = 0" 那条阈值的落地。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.pii import (  # noqa: E402
    PiiType,
    Strategy,
    classify_sensitivity,
    detect,
    redact,
    redact_for_index,
    redact_for_trace,
)

# 一段"什么都踩"的危险文本：7 类 PII/密钥全齐
DANGER = (
    "差旅报销联系人 finance@acme.com，热线 13812345678。\n"
    "轮值同学身份证 310101199001011234，报销卡号 6222021234567890123。\n"
    "生产库 DSN: postgresql://svc_rag:Pr0dP%40ss@10.20.30.40:5432/kb\n"
    "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdEFGH1234\n"
    "AWS key AKIAIOSFODNN7EXAMPLE\n"
)

# 入库/落日志后**绝不允许**出现的明文
MUST_NOT_LEAK = [
    "13812345678",
    "310101199001011234",
    "6222021234567890123",
    "Pr0dP%40ss",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiJ9",
]


def test_detects_all_seven_categories():
    kinds = {h.pii_type for h in detect(DANGER)}
    for must in [
        PiiType.EMAIL,
        PiiType.CN_MOBILE,
        PiiType.CN_ID_CARD,
        PiiType.BANK_CARD,
        PiiType.DB_DSN,
        PiiType.JWT,
        PiiType.API_KEY,
    ]:
        assert must in kinds, f"漏检 {must.value}；实际检出 {[k.value for k in kinds]}"


def test_redact_for_index_leak_rate_is_zero():
    """★ 核心回归：写入向量库的文本泄漏率必须为 0。"""
    report = redact_for_index(DANGER)
    for leaked in MUST_NOT_LEAK:
        assert leaked not in report.text, f"PII 泄漏进向量库: {leaked}"
    assert not report.clean, "应报告命中了 PII"
    assert report.counts, "应有命中统计（可观测）"


def test_redact_for_trace_leak_rate_is_zero():
    """L14 README 写了"PII 不得进 trace"却没实现 —— 这里是它的回归测试。"""
    out = redact_for_trace(DANGER, max_len=10_000)
    for leaked in MUST_NOT_LEAK:
        assert leaked not in out, f"PII 泄漏进 trace: {leaked}"


def test_secrets_are_dropped_not_merely_masked():
    """密钥类必须彻底 DROP —— 留下 hash 既没检索价值又有风险。"""
    out = redact_for_index("DSN postgresql://u:p@h:5432/db and key AKIAIOSFODNN7EXAMPLE").text
    assert "<DB_DSN>" in out
    assert "<API_KEY>" in out
    assert "AKIA" not in out


def test_mask_preserves_tail_for_searchability():
    """个人信息用 MASK 保留尾号，保住一点可检索性。"""
    out = redact("手机 13812345678", strategy=Strategy.MASK).text
    assert "13812345678" not in out
    assert "138" in out and "5678" in out, "应保留前3尾4"


def test_hash_strategy_is_stable_for_equality_matching():
    """同值同 hash → 还能做等值匹配（比如"同一个人的多张单据"）。"""
    a = redact("联系 13812345678", strategy=Strategy.HASH).text
    b = redact("另一处 13812345678", strategy=Strategy.HASH).text
    tag_a = a[a.index("<") : a.index(">") + 1]
    tag_b = b[b.index("<") : b.index(">") + 1]
    assert tag_a == tag_b, "同一号码的 hash 标签必须一致"


def test_email_mask_keeps_domain():
    out = redact("写信到 alice@acme.com", strategy=Strategy.MASK).text
    assert "alice@acme.com" not in out
    assert "@acme.com" in out, "域名保留（可判断是内部还是外部邮箱）"


def test_no_false_positive_on_clean_text():
    clean = "本季度营收增长 12%，客户满意度提升到 4.8 分，团队规模 30 人。"
    report = redact_for_index(clean)
    assert report.clean, f"干净文本不该误报: {report.counts}"
    assert report.text == clean, "干净文本不该被改写"


def test_overlapping_matches_do_not_corrupt_text():
    """DSN 里含密码，不能被 API_KEY 规则再切一刀切成乱码。"""
    out = redact_for_index("postgresql://svc:Pr0dP%40ss@10.20.30.40:5432/kb").text
    assert out.count("<DB_DSN>") == 1
    assert "Pr0dP" not in out


def test_long_digit_string_not_misread_as_phone():
    """负向断言：订单号里的一段不该被当成手机号（lookbehind 生效）。"""
    hits = detect("订单号 613812345678901234")
    assert PiiType.CN_MOBILE not in {h.pii_type for h in hits}


# ── 敏感度自动分级 ───────────────────────────────────
def test_sensitivity_upgrades_on_id_card():
    final, reasons = classify_sensitivity("员工身份证 310101199001011234", "public")
    assert final == "restricted", "含身份证必须升到 restricted"
    assert "cn_id_card" in reasons


def test_sensitivity_email_only_stays_public():
    final, _ = classify_sensitivity("联系邮箱 hr@acme.com", "public")
    assert final == "public", "仅邮箱不该过度升级（否则全库都变 restricted）"


def test_sensitivity_never_downgrades():
    """人可能知道机器不知道的上下文 —— 安全侧永远取更严的那个。"""
    final, _ = classify_sensitivity("完全没有 PII 的战略文档", "restricted")
    assert final == "restricted"


def test_sensitivity_mobile_upgrades_to_internal():
    final, _ = classify_sensitivity("热线 13812345678", "public")
    assert final == "internal"
