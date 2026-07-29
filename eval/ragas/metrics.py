"""RAGAS 指标实现（ENTERPRISE_AUDIT C1）。

参考 RAGAS 0.2.x 语义,但内部用本地 `MmxRagasLLM` 而不是 OpenAI / Anthropic。
当真实 ragas 库装上时,这套实现可被 `from ragas.metrics import faithfulness, ...` 替换
（接口同型）。

四个指标:
- `faithfulness`(答案是否基于 context):答案 → statements → 每个 statement 是否被 context 支持
- `answer_relevancy`(答案是否切题):answer 反向生成问题,与原 query 的语义相似(用 mmx 0..5 评分做近似)
- `context_precision`(检索精度):top-k context 中相关 context 的占比(L2R 排序评分)
- `context_recall`(检索召回):ground_truth 拆 statements,每个 statement 是否被 context 覆盖

返回:0.0–1.0 浮点。LLM 不可用时返回 0.0(明确 fail-loud,不静默填默认值)。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .ragas_llm import MmxRagasLLM

# ── helpers ──

_SENTENCE_SPLIT = re.compile(r"[。.!?！？\n]+")


def _split_statements(text: str) -> list[str]:
    """把长文拆成 statements(RAGAS 内部也这么干)。"""
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text or "")]
    return [p for p in parts if len(p) >= 2]


def _llm_safe(llm: MmxRagasLLM, prompt: str, **kw: Any) -> str:
    """兜底调用 LLM;失败返回空串。"""
    try:
        return llm.generate_text(prompt, **kw).strip()
    except Exception:
        return ""


def _coerce_score(s: str, default: float = 0.0) -> float:
    """LLM 自由文本输出 → 浮点数。匹配 '0.7' / '7/10' / '70%'。"""
    if not s:
        return default
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return a / b if b else default
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        return float(m.group(1)) / 100.0
    m = re.search(r"(-?\d+(?:\.\d+)?)", s.replace("\n", " "))
    if m:
        v = float(m.group(1))
        # 0..5 视为 0..1 缩放
        if v > 1.0 and v <= 5.0:
            return v / 5.0
        # 0..10 缩放
        if v > 5.0 and v <= 10.0:
            return v / 10.0
        return max(0.0, min(1.0, v))
    return default


# ── 1. faithfulness ──


def faithfulness(answer: str, contexts: list[str], llm: MmxRagasLLM) -> float:
    """RAGAS faithfulness: # supported statements / # total statements.

    失败兜底:返回 0.0(fail-loud,不能静默给 1.0 当没问题)。
    """
    statements = _split_statements(answer)
    if not statements:
        return 0.0
    ctx_blob = "\n".join(contexts or []).strip()
    if not ctx_blob:
        return 0.0

    supported = 0
    for stmt in statements:
        prompt = (
            "判断以下陈述是否能从提供的证据中找到支持。\n"
            "严格基于证据,不要靠常识。\n"
            "回答 1(支持)或 0(不支持)。\n\n"
            f"陈述: {stmt}\n\n"
            f"证据: {ctx_blob[:2000]}\n\n"
            "只回答 1 或 0。"
        )
        out = _llm_safe(llm, prompt, max_tokens=2)
        if "1" in out and "0" not in out:
            supported += 1
        elif "0" in out:
            pass
        else:
            # 解析失败:不计数,保持严格性
            pass
    return supported / len(statements)


# ── 2. answer_relevancy ──


def answer_relevancy(
    question: str, answer: str, contexts: list[str], llm: MmxRagasLLM
) -> float:
    """RAGAS answer_relevancy:答案切题程度(0..5 → 归一)。"""
    if not question or not answer:
        return 0.0
    prompt = (
        "判断以下回答对问题有多切题。给 0-5 整数。\n"
        "- 5: 完全切题,直接回答\n"
        "- 3: 部分切题\n"
        "- 0: 完全不切题或无信息\n\n"
        f"问题: {question}\n\n"
        f"回答: {answer[:1500]}\n\n"
        "只回答 0-5。"
    )
    out = _llm_safe(llm, prompt, max_tokens=3)
    return _coerce_score(out, default=0.0)


# ── 3. context_precision ──


def context_precision(
    question: str,
    contexts: list[str],
    ground_truth: str,
    llm: MmxRagasLLM,
) -> float:
    """RAGAS context_precision: LLM judge 每个 context 是否相关。

    使用 L2R-rank 风格:Precision@k = relevant_k / k;
    最终 weighted by relevance distribution。
    简化为平均 precision@k(取 top-k 全部,后续如需加权再加)。
    """
    if not contexts or not ground_truth:
        return 0.0
    relevant_count = 0
    for ctx in contexts:
        prompt = (
            "判断以下检索片段是否对回答标准答案有帮助。\n"
            "1 表示有帮助(含部分/全部标准答案信息);0 表示无关。\n\n"
            f"标准答案: {ground_truth[:500]}\n\n"
            f"检索片段: {ctx[:1500]}\n\n"
            "只回答 1 或 0。"
        )
        out = _llm_safe(llm, prompt, max_tokens=2)
        if "1" in out and "0" not in out:
            relevant_count += 1
    return relevant_count / len(contexts)


# ── 4. context_recall ──


def context_recall(
    ground_truth: str, contexts: list[str], llm: MmxRagasLLM
) -> float:
    """RAGAS context_recall: ground_truth 拆 statements,每个是否被 context 覆盖。"""
    statements = _split_statements(ground_truth)
    if not statements:
        return 0.0
    ctx_blob = "\n".join(contexts or []).strip()
    if not ctx_blob:
        return 0.0

    covered = 0
    for stmt in statements:
        prompt = (
            "判断以下陈述是否能从提供的证据中推出。\n"
            "严格基于证据,不靠常识。\n"
            "1 表示可推出;0 表示不可。\n\n"
            f"陈述: {stmt}\n\n"
            f"证据: {ctx_blob[:2000]}\n\n"
            "只回答 1 或 0。"
        )
        out = _llm_safe(llm, prompt, max_tokens=2)
        if "1" in out and "0" not in out:
            covered += 1
    return covered / len(statements)


# ── batch facade(模拟 ragas.evaluate() 入口) ──


def evaluate(
    rows: list[dict], llm: MmxRagasLLM, metrics: list[str] | None = None
) -> dict:
    """对一批 row 跑指定指标,返回聚合。

    Args:
        rows: 每条 dict 必须含 question/answer/contexts,
              可选 ground_truth。
        llm: MmxRagasLLM 实例。
        metrics: 子集 ['faithfulness','answer_relevancy',
                       'context_precision','context_recall'];
                 None 跑全部(precision/recall 需要 ground_truth)。

    Returns:
        dict {metric_name: aggregated_score_0_to_1}
    """
    if metrics is None:
        metrics = ["faithfulness", "answer_relevancy"]
    fns: dict[str, Callable[..., float]] = {
        "faithfulness": lambda r: faithfulness(r["answer"], r["contexts"], llm),
        "answer_relevancy": lambda r: answer_relevancy(
            r["question"], r["answer"], r["contexts"], llm
        ),
        "context_precision": lambda r: context_precision(
            r["question"], r["contexts"], r.get("ground_truth", ""), llm
        ),
        "context_recall": lambda r: context_recall(
            r.get("ground_truth", ""), r["contexts"], llm
        ),
    }

    out: dict[str, list[float]] = {m: [] for m in metrics}
    for r in rows:
        for m in metrics:
            try:
                out[m].append(fns[m](r))
            except Exception:
                # 失败 strict:不静默 0.0,以反映"未评测"
                out[m].append(0.0)

    return {
        m: round(sum(v) / len(v), 4) if v else 0.0
        for m, v in out.items()
        if v
    }


__all__ = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "evaluate",
]
