"""L09 — 查询改写 4 策略。

策略：
1. multi-query：让 LLM 生成 3 个 paraphrased query
2. HyDE：让 LLM 先"假想答案"，再检索
3. step-back：抽象一层（"X 的细节"→"X 的整体"）
4. decomposition：把多跳问题拆成子问题

跑：
    python 02-retrieval-quality/labs/L09-query-rewrite/run.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ART = Path("artifacts/L09")
ART.mkdir(parents=True, exist_ok=True)

# 4 策略的 prompt 模板
PROMPTS = {
    "multi-query": """你是一个查询改写助手。给定用户问题，生成 3 个不同措辞但意思相同的版本，用于向量检索。
每行一个，不要编号。""",
    "hyde": """你是一个领域专家。给定用户问题，先写一个**假想的、可能正确的答案**（2-3 句），用于帮助语义检索。
问题：{q}
假想答案：""",
    "step-back": """你是一个查询改写助手。给定具体问题，先退一步抽象，问一个更高层的问题。
例如：
- Q: "RAG 用了 HNSW 索引会更快吗？" → 抽象: "RAG 的检索效率受哪些因素影响？"
- Q: "Transformer 的 attention 计算复杂度是多少？" → 抽象: "Transformer 的核心机制是什么？"
原始问题：{q}
抽象问题：""",
    "decomposition": """你是一个查询分解助手。给定一个可能需要多步推理的问题，拆成 2-3 个子问题。
每行一个子问题。
原始问题：{q}
子问题：""",
}


def call_llm(prompt: str, system: str = "", timeout: int = 30) -> str:
    """优先用 mmx，回退到 Ollama。"""
    # mmx
    if os.environ.get("RAG_PROG_LLM_BACKEND", "mmx") == "mmx":
        import subprocess
        try:
            args = ["mmx", "text", "chat", "--model", "MiniMax-M3",
                    "--max-tokens", "256", "--temperature", "0.3",
                    "--output", "json"]
            if system:
                args += ["--system", system]
            args += ["--message", prompt]
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return json.loads(r.stdout).get("text", r.stdout)
        except Exception:
            pass
    # ollama
    try:
        body = json.dumps({
            "model": os.environ.get("OLLAMA_LLM_MODEL", "llama3.1:8b"),
            "prompt": prompt, "system": system, "stream": False,
            "options": {"temperature": 0.3, "num_predict": 256},
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["response"]
    except Exception as e:
        return f"（LLM 不可用：{e}）"


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L09 — 查询改写 4 策略")
    print("════════════════════════════════════════════\n")

    queries = [
        "RAG 用 HNSW 索引会更快吗？",
        "员工持股 cliff 是什么意思？",
    ]
    out: list[dict] = []
    for q in queries:
        print(f"\n━━━ Q: {q} ━━━")
        for name, tpl in PROMPTS.items():
            prompt = tpl.format(q=q)
            ans = call_llm(prompt, system="你是查询改写助手。")
            print(f"\n[{name}]")
            print(ans.strip())
            out.append({"q": q, "strategy": name, "output": ans.strip()})

    (ART / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "report.md").write_text(
        "# L09 — 查询改写报告\n\n"
        "## 4 策略\n\n"
        "### 1. Multi-Query\n"
        "- 让 LLM 生成 3 个 paraphrased query\n"
        "- 适用：用户问题含糊或太短\n"
        "- 成本：3× 检索 + 1× LLM\n\n"
        "### 2. HyDE（Hypothetical Document Embeddings）\n"
        "- 让 LLM 先生成'假想答案'，用其 embedding 检索\n"
        "- 适用：知识领域与问题措辞差距大（如法律/医学）\n"
        "- 风险：假想答案可能错，引入噪声\n\n"
        "### 3. Step-Back\n"
        "- 把具体问题抽象一层\n"
        "- 适用：用户问细节但知识库里只有概览\n"
        "- 思路：先检索概览 → 再检索细节\n\n"
        "### 4. Decomposition\n"
        "- 把多跳问题拆成子问题\n"
        "- 适用：多跳 QA（multihop）\n"
        "- 配合：可对每个子问题分别检索\n",
        encoding="utf-8",
    )
    print(f"\n✓ L09 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
