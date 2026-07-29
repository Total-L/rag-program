"""P1 abstention 启发式收紧测试（根因 3 修复）。

背景（详见 RCA_p1_eval_regression.md 根因 3）：
- 旧 heuristic 总是覆盖 LLM 的 JSON abstained=false，导致：
  - 含「我无法在知识库中找到」前缀但实际给了答案的题目被错误拒答
  - 含「找不到」/「知识库中没」等常见词的答案被错误拒答
- 旧 markers 过宽：「找不到」/「知识库中没」/「我无法」都是常见词
- 旧规则无 fallback 概念：无论 JSON 是否解析成功都跑 heuristic

修复后契约（见 pipeline._parse_llm_response）：
1. JSON 解析成功 → 尊重 LLM abstained 字段，heuristic 不覆盖
2. JSON 解析失败 → heuristic 启用，但 markers 收紧为完整短语（前 30 字匹配）
3. markers: ["我无法在知识库中找到", "无法在知识库中找到", "I cannot find", "no information"]
"""

from __future__ import annotations

from pipeline import _parse_llm_response  # noqa: E402

# ---- JSON 解析成功：heuristic 不覆盖 ----


def test_json_abstained_false_kept_with_old_markers():
    """修复前 bug：JSON abstained=false + answer 含「找不到」 → 被错误标 abstained=True。
    修复后：JSON 成功 → abstained 保持 False。
    """
    raw = '{"answer":"关于此问题，在 hr-001 文档里找不到具体数字，但根据上下文是 5 天。","citations":["E1"],"abstained":false}'
    answer, citations, abstained = _parse_llm_response(raw)
    assert abstained is False, "JSON abstained=false 时 heuristic 不应覆盖"
    assert citations == ["E1"]


def test_json_abstained_false_kept_with_knowledge_base_marker():
    """修复前 bug：JSON abstained=false + answer 含「知识库中没」 → 被错误标 abstained=True。"""
    raw = '{"answer":"知识库中没有提到具体天数，但通常员工有 5 天年假","citations":["E1"],"abstained":false}'
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is False


def test_json_abstained_true_kept():
    """回归守护：JSON abstained=true 时不丢失。"""
    raw = '{"answer":"我无法在知识库中找到相关信息","citations":[],"abstained":true}'
    answer, _, abstained = _parse_llm_response(raw)
    assert abstained is True
    assert "我无法" in answer


# ---- JSON 解析失败：heuristic 启用，但收紧 ----


def test_json_fail_full_abstain_phrase_triggers():
    """JSON 解析失败 + answer 是完整拒答短语（前 30 字含「我无法在知识库中找到」） → abstained=True。"""
    raw = "我无法在知识库中找到相关信息，请换个问题。"
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is True


def test_json_fail_short_abstain_phrase_triggers():
    """JSON 解析失败 + answer 短且是拒答短语 → abstained=True。"""
    raw = "无法在知识库中找到"
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is True


def test_json_fail_normal_answer_not_triggered():
    """JSON 解析失败 + answer 是正常回答（含数字/事实）→ abstained=False。"""
    raw = "员工年假是 5 天，司龄 1-3 年 10 天。具体可参考 hr-001 文档。"
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is False, "JSON 解析失败 + 正常答案不应被启发式误标 abstained"


def test_json_fail_partial_marker_not_triggered():
    """JSON 解析失败 + answer 前 30 字含「找不到」（过宽 marker 移除） → abstained=False。"""
    raw = "找不到 hr-001 文档的实际位置，但根据 memory 是 5 天年假。"
    _, _, abstained = _parse_llm_response(raw)
    # 「找不到」不在 _ABSTAIN_PHRASES 里（前缀「我无法在知识库中找到」也不匹配）
    # 所以不应触发
    assert abstained is False


def test_json_fail_marker_after_30_chars_not_triggered():
    """JSON 解析失败 + marker 出现在 answer 第 30 字之后 → abstained=False。
    这模拟 LLM 在长答案中插入拒答短语的情况。
    """
    long_answer = (
        "员工年假政策：司龄 < 1 年 5 天；1-3 年 10 天；3 年以上 15 天；详细可参考 hr-001 文档。" * 2
        + " 我无法在知识库中找到更多信息"
    )
    assert len(long_answer) > 100, f"测试装置错误：len={len(long_answer)}"
    _, _, abstained = _parse_llm_response(long_answer)
    assert abstained is False, "marker 超出前 30 字 → 不应触发（heuristic 仅看 head）"


# ---- 边界 ----


def test_empty_raw():
    """空字符串 → JSON 解析失败 + head 为空 → abstained=False。"""
    _, _, abstained = _parse_llm_response("")
    assert abstained is False


def test_invalid_json_with_abstain_phrase():
    """无效 JSON + answer 是拒答短语 → abstained=True。"""
    raw = "Not a JSON but: 我无法在知识库中找到相关信息"
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is True


def test_english_abstain_phrase():
    """英文拒答短语触发。"""
    raw = "I cannot find information about this in the knowledge base."
    _, _, abstained = _parse_llm_response(raw)
    assert abstained is True
