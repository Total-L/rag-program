"""P6 数据优化层单测：清洗 / 归一化 / 近重复 / 质量过滤。

覆盖意图：证明"优化"真的改变了下游行为，而不只是函数跑通。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.clean import (  # noqa: E402
    NearDupIndex,
    detect_language,
    find_repeated_lines,
    jaccard,
    minhash,
    normalize_text,
    quality_check,
    strip_boilerplate,
)

# 内容各异的"长文档"。刻意不用 "一句话 * N"：shingles 是 set，
# 重复内容不会让集合变大，会让相似度实验得出错误结论。
LONG_DOC = (
    "第一条 本报销制度适用于公司全体正式员工及实习生。\n"
    "第二条 差旅费用包括交通、住宿、餐饮三类，需分项填报。\n"
    "第三条 市内交通每人每天上限二百元，需提供正规票据。\n"
    "第四条 住宿标准一线城市每晚八百元，二线城市每晚五百元。\n"
    "第五条 餐饮补贴每人每天一百元，无需提供票据。\n"
    "第六条 单笔支出超过五千元的，必须事先取得部门总监书面批准。\n"
    "第七条 所有报销申请须在费用发生后三十日内提交，逾期不予受理。\n"
    "第八条 财务部门收到申请后五个工作日内完成审核并支付。\n"
    "第九条 虚报冒领者按公司纪律处理，情节严重的移交司法机关。\n"
    "第十条 本制度自二零二六年一月一日起施行，原制度同时废止。\n"
)
# 整篇只改一处措辞
LONG_DOC_TWEAKED = LONG_DOC.replace("逾期不予受理", "逾期原则上不受理")
UNRELATED_DOC = (
    "本季度产品路线图聚焦多格式文档解析能力的全面升级。\n"
    "我们将支持扫描件的光学字符识别与复杂版面分析。\n"
    "检索侧引入混合召回与交叉编码器重排序两项改进。\n"
    "运维侧建设增量索引与向量库对账机制降低成本。\n"
    "预计第三季度末完成灰度上线并开放给内部试用团队。\n"
)


# ── 归一化：增量索引能生效的前提 ──────────────────────
def test_normalize_makes_equivalent_text_hash_identical():
    """全角/零宽/换行差异必须归一 —— 否则每次摄取都误判"内容变了"而重嵌。"""
    a = normalize_text("报销限额为 800 元，含税")
    b = normalize_text("报销限额为 800 元，含税​")  # 带零宽空格
    c = normalize_text("报销限额为 800 元，含税\r\n")  # CRLF

    def h(s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()

    assert a == b == c
    assert h(a) == h(b) == h(c), "等价文本必须算出同一个 hash"


def test_normalize_strips_invisibles_and_collapses_space():
    out = normalize_text("正文​﻿　　多空格\r\n\n\n\n下一段")
    assert "​" not in out and "﻿" not in out
    assert "\r" not in out
    assert "  " not in out, "连续空格应折叠"
    assert "\n\n\n" not in out, "3+ 空行应折叠"


def test_normalize_empty_is_safe():
    assert normalize_text("") == ""


# ── 去样板 ──────────────────────────────────────────
def test_strip_boilerplate_removes_pattern_lines():
    text = "正文第一段\n第 3 页\nPage 7 of 12\n- 12 -\n公司内部资料\n© 2026 ACME\n正文第二段"
    cleaned, stats = strip_boilerplate(text)
    assert "正文第一段" in cleaned and "正文第二段" in cleaned
    for junk in ["第 3 页", "Page 7", "- 12 -", "内部资料", "© 2026"]:
        assert junk not in cleaned, f"样板未清除: {junk}"
    assert stats.removed_lines == 5


def test_find_repeated_lines_discovers_company_specific_footer():
    """写死正则覆盖不了每家公司自己的页脚，必须靠跨文档频率发现。"""
    docs = [f"# 标题{i}\nACME 保留所有权利\n正文{i}" for i in range(8)]
    rep = find_repeated_lines(docs)
    assert "ACME 保留所有权利" in rep
    assert not any(f"正文{i}" in rep for i in range(8)), "正文不该被误判为样板"
    cleaned, _ = strip_boilerplate(docs[0], repeated_lines=rep)
    assert "ACME" not in cleaned and "正文0" in cleaned


def test_find_repeated_lines_empty_corpus():
    assert find_repeated_lines([]) == set()


def test_strip_boilerplate_drops_orphaned_headings():
    """★ 实测发现的坑：样板答案被删后，标题会孤零零留下。

    `### 谁负责 X?` 的答案是全语料通用样板句，删掉后标题独自存在 ——
    它零信息量却会稀释 chunk 的嵌入语义，必须一起清掉。
    """
    text = (
        "# 付款流程\n\n网银付款需双人复核。\n\n"
        "### 谁负责 付款流程?\n\n由对应部门 owner 负责。\n\n"
        "### 什么是 付款流程?\n\n网银付款需双人复核。\n"
    )
    cleaned, _ = strip_boilerplate(text, repeated_lines={"由对应部门 owner 负责。"})
    assert "谁负责" not in cleaned, "答案被删后，孤儿标题也应删掉"
    assert "什么是 付款流程?" in cleaned, "有内容的标题必须保留"
    assert "网银付款需双人复核。" in cleaned


def test_strip_boilerplate_keeps_heading_with_content():
    text = "# 标题\n\n这里有实际内容需要保留。\n"
    cleaned, _ = strip_boilerplate(text)
    assert "# 标题" in cleaned
    assert "实际内容" in cleaned


def test_strip_boilerplate_drops_trailing_empty_heading():
    cleaned, _ = strip_boilerplate("# 有内容\n\n正文。\n\n## 结尾空标题\n")
    assert "结尾空标题" not in cleaned, "末尾没有内容的标题也应删掉"
    assert "正文。" in cleaned


# ── 语言检测 ─────────────────────────────────────────
def test_detect_language():
    assert detect_language("这是一段纯中文的企业政策文档内容说明") == "zh"
    assert detect_language("This is a purely english policy document") == "en"
    assert detect_language("本政策 policy 适用于 all employees 全体员工") == "mixed"
    assert detect_language("") == "unknown"
    assert detect_language("123 !!!") == "unknown", "无字母字符应判 unknown 而非瞎猜"


# ── 质量过滤 ─────────────────────────────────────────
def test_quality_check_rejects_junk():
    ok = quality_check(
        "本报销制度适用于公司全体正式员工及实习生，差旅费用包括交通、住宿、餐饮三类。"
    )
    assert ok.ok, f"正常正文不该被拒: {ok.reason}"

    assert not quality_check("短").ok
    assert quality_check("短").reason.startswith("too_short")

    toc = quality_check("1. 总则.......... 3\n2. 范围.......... 7\n3. 附则.......... 9")
    assert not toc.ok and toc.reason == "looks_like_toc", "目录页应被挡在库外"

    sym = quality_check("!!!@@@###$$$%%%^^^&&&***((()))___+++===~~~```")
    assert not sym.ok, "纯符号（OCR 噪点）应被挡住"

    nums = quality_check("1234567890 ....... 12, 34. 56、78。90 - - - _ _ _")
    assert not nums.ok


# ── 手写 MinHash 近重复 ───────────────────────────────
def test_minhash_similarity_ordering():
    """长文档里的小改动应仍判为高度相似（这才是真实去重场景）。

    注意用**内容各异**的长文本，不能用 "一句话 * N" ——
    shingles() 返回 set，重复内容不会让集合变大（见 shingles 文档）。
    """
    sim_dup = jaccard(minhash(LONG_DOC), minhash(LONG_DOC_TWEAKED))
    sim_diff = jaccard(minhash(LONG_DOC), minhash(UNRELATED_DOC))
    assert sim_dup > 0.9, f"长文档改一处应仍高度相似, got {sim_dup}"
    assert sim_diff < 0.2, f"无关文本应低相似, got {sim_diff}"
    assert sim_dup > sim_diff


def test_minhash_threshold_must_be_calibrated_to_length():
    """★ 实测校准：短句上一处 2 字改动只有 ~0.66 相似度。

    这条测试把"阈值必须按文本长度校准"这个坑固化下来：
    用文档级阈值 0.92 去抓短句近重复一定会漏。
    只断言**确定性**的相似度数值，不断言 LSH 的概率性命中结果。
    """
    short_a = "员工差旅报销需在三十天内提交申请，单笔超过五千元需要总监审批后方可报销。"
    short_b = short_a.replace("方可", "才可")
    sim_short = jaccard(minhash(short_a), minhash(short_b))
    sim_long = jaccard(minhash(LONG_DOC), minhash(LONG_DOC_TWEAKED))
    assert 0.5 < sim_short < 0.8, f"短句 2 字改动实测区间, got {sim_short}"
    assert sim_long > sim_short + 0.15, (
        f"同样改一处，长文档相似度({sim_long:.2f}) 必须明显高于短句({sim_short:.2f})"
        " —— 这就是阈值必须按长度校准的原因"
    )


def test_minhash_is_deterministic():
    """确定性很重要：否则同一文档两次摄取算出不同签名，去重结果不可复现。"""
    assert minhash("一段测试文本内容") == minhash("一段测试文本内容")


def test_minhash_identical_text_is_1():
    t = "完全一样的一段文本内容用于测试相似度"
    assert jaccard(minhash(t), minhash(t)) == 1.0


def test_neardup_index_catches_tweaked_duplicate():
    """文档级近重复：整篇只改一处措辞，必须被抓到。"""
    idx = NearDupIndex(threshold=0.85)
    assert idx.add("d1", LONG_DOC) == [], "首条无重复"
    dups = idx.add("d2", LONG_DOC_TWEAKED)
    assert dups and dups[0][0] == "d1", f"近重复必须被抓到, got {dups}"
    assert idx.add("d3", UNRELATED_DOC) == [], "无关文档不得误判"
    assert len(idx) == 3


def test_neardup_rejects_bad_band_config():
    import pytest

    with pytest.raises(ValueError):
        NearDupIndex(num_perm=64, bands=7)  # 64 不能被 7 整除
