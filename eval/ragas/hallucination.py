"""幻觉检测指标(ENTERPRISE_AUDIT C1)。

三种口径:
- `unsupported_claims_rate`:用 mmx 列出 answer 中所有找不到
  context 支撑的具体声明,rate = # unsupported / # total。
  比 RAGAS faithfulness 更直观。
- `hallucination_rate`: 1 - RAGAS faithfulness。
  (RAGAS 0.2 faithfulness 输出越高 → 越不 hallucinate。)
- `numeric_hallucination_rate`: 用 regex 抽 answer 里的所有
  数字/日期/人名,核对是否都在 context 中出现。
  捕获最严重的"编造数字/日期"型幻觉。

失败兜底:返回 0.0(strict),不静默。
"""
from __future__ import annotations

import re

from .ragas_llm import MmxRagasLLM

_NUMERIC_RE = re.compile(
    r"(\d{4}|\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)"
)
_DATE_RE = re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")


def _llm_safe(llm: MmxRagasLLM, prompt: str, **kw) -> str:
    try:
        return llm.generate_text(prompt, **kw).strip()
    except Exception:
        return ""


def unsupported_claims_rate(
    answer: str, contexts: list[str], llm: MmxRagasLLM
) -> float:
    """列出 answer 中无 context 支撑的声明,返回 unsupported/total。

    输出格式:
        unsupported_count / total_count
    (避免自由文本难解析)
    """
    if not answer or not contexts:
        return 0.0
    ctx_blob = "\n".join(contexts).strip()
    prompt = (
        "请列出回答中的所有具体事实声明(人名/数字/日期/政策条款/技术指标),"
        "格式用换行分隔,每条单独一行。然后在最后一行输出 'unsupported: N' "
        "表示其中无法从证据推出的数量。\n\n"
        f"回答: {answer[:1500]}\n\n"
        f"证据: {ctx_blob[:2500]}\n\n"
        "输出:\n- [声明 1]\n- [声明 2]\n...\nunsupported: N"
    )
    out = _llm_safe(llm, prompt, max_tokens=500)
    m = re.search(r"unsupported[:：]\s*(\d+)", out.lower())
    if not m:
        return 0.0
    # 总声明数:数 - 开头
    claims = len(re.findall(r"^[\s\-\*]\s*\S+", out, re.MULTILINE))
    unsupported = int(m.group(1))
    if claims == 0:
        return 0.0
    return round(min(unsupported, claims) / claims, 4)


def hallucination_rate(answer: str, contexts: list[str], llm: MmxRagasLLM) -> float:
    """1 - RAGAS faithfulness。值越小越好。"""
    from .metrics import faithfulness

    f = faithfulness(answer, contexts, llm)
    return round(1.0 - f, 4)


def numeric_hallucination_rate(
    answer: str, contexts: list[str], *_unused: object
) -> float:
    """数字幻觉率:answer 中出现的数字/日期,在 context 中不存在的比例。

    纯 stdlib 实现(不用 LLM),快且可重现。
    """
    if not answer or not contexts:
        return 0.0
    ctx_blob = "\n".join(contexts)
    ans_numbers = set(_NUMERIC_RE.findall(answer))
    date_strs = set(_DATE_RE.findall(answer))
    ans_items = ans_numbers | date_strs
    if not ans_items:
        return 0.0
    missing = sum(1 for x in ans_items if x not in ctx_blob)
    return round(missing / len(ans_items), 4)


__all__ = [
    "unsupported_claims_rate",
    "hallucination_rate",
    "numeric_hallucination_rate",
]
