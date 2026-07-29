"""L03 RAG 评测入口:复用 L03 run.py 的 ollama_embed / cosine_topk / mmx_generate / ollama_generate,
跑 01-foundations/labs/_shared/lab_golden.jsonl 的 8 题,产出 artifacts/L03/l03_raw.jsonl。

不修改 L03 run.py 本体(保留 demo 行为不变);只把生成/检索函数拿过来用,
context pack + backend fallback 逻辑内联在此处。

跑:
    python 01-foundations/labs/L03-minimal-rag/run_eval.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 共享语料 + golden 题库
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from _shared.lab_corpus import CORPUS as CORPUS_DOCS, DOCS_TEXT as CORPUS, SYSTEM_PROMPT  # noqa: E402

# 复用 L03 的工具函数
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import ollama_embed, cosine_topk, mmx_generate, ollama_generate  # noqa: E402

ART = Path("artifacts/L03")
ART.mkdir(parents=True, exist_ok=True)

ABSTAIN_PATTERNS = ("无法在知识库中找到", "找不到相关信息", "我不确定", "没有足够信息")


def pack(retrieved: list[str]) -> str:
    return "\n".join(f"[{i+1}] {t}" for i, t in enumerate(retrieved))


def _extract_text(raw: str) -> str:
    """mmx `--output json` 现在返回 Anthropic 格式(JSON object, 答案在
    content[0].text);L03 的 mmx_generate 只 .get('text') 拿不到。
    这里兼容多种格式:text / content[0].text / 裸字符串 / 原样。
    """
    s = raw.strip()
    if not s.startswith("{"):
        return raw
    try:
        d = json.loads(s)
        if isinstance(d.get("text"), str):
            return d["text"]
        content = d.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                return first["text"]
        return raw  # 实在认不出就回原样,eval 仍能跑
    except Exception:
        return raw


def generate(user_prompt: str) -> tuple[str, str]:
    """复刻 L03 main() 的 backend fallback:mmx 优先,失败回退 ollama。"""
    try:
        return _extract_text(mmx_generate(user_prompt, system=SYSTEM_PROMPT)), "mmx"
    except Exception as e:
        try:
            return _extract_text(ollama_generate(user_prompt, system=SYSTEM_PROMPT)), "ollama"
        except Exception as e2:
            return f"（生成失败：mmx={e}, ollama={e2}）", "failed"


def main() -> None:
    # 1) 一次性嵌入 8 篇语料
    print(f"→ 嵌入语料 ({len(CORPUS)} 篇)...")
    doc_vecs = ollama_embed(CORPUS)
    print(f"  embedding dim={len(doc_vecs[0])}")

    # 2) 加载 slim golden
    golden_path = HERE / "_shared" / "lab_golden.jsonl"
    queries = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    log = []
    for q in queries:
        qid = q["id"]
        question = q["question"]
        must_abstain = q.get("must_abstain", False)
        print(f"\n[{qid}] Q: {question}")
        t0 = time.time()
        try:
            qv = ollama_embed([question])[0]
            top_hits = cosine_topk(qv, doc_vecs, k=3)  # list[(idx, score)]
            retrieved_docs = [CORPUS[idx] for idx, _ in top_hits]
            ctx = pack(retrieved_docs)
            user_prompt = f"上下文：\n{ctx}\n\n问题：{question}\n\n回答："
            ans, gen_backend = generate(user_prompt)
        except Exception as e:
            ans = f"（生成失败：{e}）"
            retrieved_docs = []
            gen_backend = "failed"
        latency_ms = round((time.time() - t0) * 1000, 1)
        abstained = any(kw in ans for kw in ABSTAIN_PATTERNS)
        print(f"A ({gen_backend}): {ans[:200]}")
        log.append({
            "id": qid,
            "question": question,
            "must_abstain": must_abstain,
            "retrieved_docs": retrieved_docs,
            "answer": ans,
            "abstained": abstained,
            "gen_backend": gen_backend,
            "latency_ms": latency_ms,
        })

    raw_path = ART / "l03_raw.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in log) + "\n",
        encoding="utf-8",
    )
    print(f"\n✓ 写 l03_raw.jsonl ({len(log)} 题) → {raw_path}")


if __name__ == "__main__":
    main()