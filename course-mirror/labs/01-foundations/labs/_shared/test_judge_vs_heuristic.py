"""LLM Judge vs 启发式 Grade 对照实验(8 题 slim golden)。

目的: 验证 LLM judge 是不是真的比启发式更准。
- 启发式: 看 retrieved docs 是否含 expected_doc_ids(用 doc_id 字符串)
- LLM judge: 用 mmx 让模型评 evidence 是否够答 question
- Ground truth: 启发式的判断(assume doc_id 匹配就是 "good" / 否则 "poor")

跑:
    python 01-foundations/labs/_shared/test_judge_vs_heuristic.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# L12 的 toy embed + retrieve(简化复用)
import numpy as np
from lab_corpus import CORPUS, DOCS_BY_ID


def toy_embed(text: str) -> np.ndarray:
    v = np.zeros(64, dtype=np.float32)
    for i, ch in enumerate(text):
        v[hash(ch) % 64] += 1
    return v / max(np.linalg.norm(v), 1e-9)


DOC_VECS = [toy_embed(d["text"]) for d in CORPUS]


def retrieve(query: str, k: int = 2) -> list[dict]:
    qv = toy_embed(query)
    sims = [(i, float(dv @ qv)) for i, dv in enumerate(DOC_VECS)]
    sims.sort(key=lambda x: -x[1])
    return [{"doc_id": CORPUS[i]["doc_id"], "text": CORPUS[i]["text"]}
            for i, _ in sims[:k]]


# ── 启发式 grade: retrieved 命中 expected_doc_ids 则 good ──
def heuristic_grade(question, retrieved_docs, expected_doc_ids):
    if not expected_doc_ids:  # unanswerable 题:不该取回任何 doc → 应该拒答
        return "good" if not retrieved_docs else "poor"
    retrieved_ids = {d["doc_id"] for d in retrieved_docs}
    return "good" if any(eid in retrieved_ids for eid in expected_doc_ids) else "poor"


# ── LLM judge: 真调 mmx ──
def llm_judge_grade(question, retrieved_docs):
    """走 P1 的 llm_judge 实现。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] /
                           "projects/p1-enterprise-kb/src"))
    from llm_judge import llm_grade
    evidence_text = "\n".join(f"[{d['doc_id']}] {d['text']}" for d in retrieved_docs)
    return llm_grade(question, evidence_text)


def main() -> None:
    golden_path = HERE / "lab_golden.jsonl"
    golden = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    results = []
    n_match = 0
    n_total = 0
    for g in golden:
        q = g["question"]
        retrieved = retrieve(q, k=2)
        expected = g.get("expected_doc_ids", [])
        must_abstain = g.get("must_abstain", False)

        h_grade = heuristic_grade(q, retrieved, expected)
        try:
            j_grade = llm_judge_grade(q, retrieved)
        except Exception as e:
            j_grade = f"ERROR: {type(e).__name__}"
        agree = (h_grade == j_grade) if j_grade in ("good", "poor") else False
        n_total += 1
        n_match += int(agree)

        results.append({
            "id": g["id"],
            "q": q,
            "retrieved": [d["doc_id"] for d in retrieved],
            "expected": expected,
            "must_abstain": must_abstain,
            "heuristic": h_grade,
            "judge": j_grade,
            "agree": agree,
        })

        flag = "✓" if agree else "✗"
        print(f"  {flag} [{g['id']}] {q[:30]:<30} heuristic={h_grade} judge={j_grade}")

    print()
    print(f"━━━ 一致率: {n_match}/{n_total} = {n_match/n_total:.1%} ━━━")
    # 写报告
    out = HERE.parent.parent / "artifacts" / "L12" / "judge_vs_heuristic.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "n": n_total,
        "agreement_rate": round(n_match / n_total, 4) if n_total else 0,
        "rows": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 报告 → {out}")


if __name__ == "__main__":
    main()