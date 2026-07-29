"""P1 — LLM Judge 评判证据质量。

为什么需要:
- 启发式 grade(看 abstained + 0 citations)判不出「证据是否真能回答问题」
- LLM judge 让模型自己判断,触发 CRAG 改写才真有意义

策略:
- mmx 优先(M3 质量好)
- 失败 fallback ollama(本地小模型)
- 全部失败默认返回 "good"(避免无限重试)

与 L12-agentic-rag/llm_judge.py 重复(per 训练营惯例,各 lab 自包含)。
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request


def _mmx_grade(
    question: str, evidence_text: str, model: str = "MiniMax-M3", timeout: int = 20
) -> str:
    """mmx 后端:返回 good/poor。"""
    prompt = (
        "判断以下证据能否回答问题。\n"
        "- good: 证据含明确答案(数字/事实/政策条文)\n"
        "- poor: 证据跟问题无关 / 不含具体答案\n\n"
        f"问题: {question}\n"
        f"证据:\n{evidence_text[:2000]}\n\n"
        "只输出 good 或 poor,不加任何解释。"
    )
    r = subprocess.run(
        [
            "mmx",
            "text",
            "chat",
            "--model",
            model,
            "--max-tokens",
            "10",
            "--temperature",
            "0.0",
            "--message",
            prompt,
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"mmx rc={r.returncode}")
    # mmx 输出可能是 text 字段或 Anthropic content[0].text
    raw = r.stdout.strip()
    try:
        d = json.loads(raw)
        if isinstance(d.get("text"), str):
            text = d["text"]
        elif isinstance(d.get("content"), list) and d["content"]:
            text = d["content"][0].get("text", "")
        else:
            text = raw
    except Exception:
        text = raw
    text = text.strip().lower()
    if "poor" in text:
        return "poor"
    return "good"


def _ollama_grade(
    question: str, evidence_text: str, model: str = "llama3.2:1b", timeout: int = 30
) -> str:
    """Ollama 后端:本地小模型 fallback。"""
    prompt = (
        "判断证据能否回答问题,只输出 good 或 poor。\n"
        f"问题: {question}\n证据: {evidence_text[:1500]}\n"
    )
    body = json.dumps(
        {
            "model": os.environ.get("OLLAMA_LLM_MODEL", model),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 10},
        }
    ).encode("utf-8")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = json.loads(resp.read())["response"].strip().lower()
    if "poor" in text:
        return "poor"
    return "good"


def llm_grade(question: str, evidence_text: str) -> str:
    """主入口:mmx 优先,失败 fallback ollama,都失败返回 "good"。

    Args:
        question: 用户原始问题
        evidence_text: 拼接后的证据文本(≤ 2000 字)

    Returns:
        "good" / "poor"
    """
    # mmx 优先
    if os.environ.get("P1_LLM_JUDGE_BACKEND", "mmx") == "mmx":
        try:
            return _mmx_grade(question, evidence_text)
        except Exception:
            pass
    # ollama fallback
    try:
        return _ollama_grade(question, evidence_text)
    except Exception:
        return "good"  # 默认给 good,避免无限重试


def heuristic_grade_for(question: str, citations: list[str], abstained: bool) -> str:
    """fallback 启发式:0 citations 或 abstained 即 poor。

    保留原 _grade 行为,作为 llm_grade 的对照基线 / 完全离线 fallback。
    """
    if abstained or not citations:
        return "poor"
    return "good"
