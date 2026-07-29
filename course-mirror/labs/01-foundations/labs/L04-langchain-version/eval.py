"""L04 对照实验:L03 (手写 RAG) vs L04 (LangChain LCEL)。

输入:artifacts/L03/l03_raw.jsonl + artifacts/L04/l04_raw.jsonl
      (分别由 L03-minimal-rag/run_eval.py 和 L04-langchain-version/run.py 产出)
题库:01-foundations/labs/_shared/lab_golden.jsonl(8 题)
语料:01-foundations/labs/_shared/lab_corpus.py(8 篇,带 doc_id)

打分明细:
- retrieval_recall_at_k: 命中的 retrieved_docs 是否覆盖 expected_doc_ids(按 doc_id 字符串匹配)
- contains_expected: answer 是否含 expected_answer_contains 任一关键词
- abstention_correct: must_abstain ↔ abstained 一致
- 复用 eval/ragas/run.py SCORERS 的 4 个 fake-RAGAS 指标
- latency_ms: 直接读 raw

输出:
- artifacts/L04/eval.json  ── 完整 metrics
- artifacts/L04/compare.md ── L03 vs L04 表格

跑:
    python 01-foundations/labs/L04-langchain-version/eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 路径:从 01-foundations/labs/L04-langchain-version/eval.py 出发
HERE = Path(__file__).resolve().parents[1]  # 01-foundations/labs
ROOT = Path(__file__).resolve().parents[3]  # /Users/totallai/rag_program (workspace root)
# 本脚本叫 eval.py,会跟 workspace 的 eval/ 包冲突;先把脚本目录踢出 sys.path
sys.path = [p for p in sys.path if not p.endswith("L04-langchain-version")]
sys.path.insert(0, str(ROOT))  # 让 eval.ragas.run 可导入
sys.path.insert(0, str(HERE))  # _shared 可导入

from eval.ragas.run import RagasRow, SCORERS  # noqa: E402
from _shared.lab_corpus import CORPUS as CORPUS_DOCS, DOCS_BY_ID  # noqa: E402

ART_L03 = HERE.parent.parent / "artifacts" / "L03" / "l03_raw.jsonl"
ART_L04 = HERE.parent.parent / "artifacts" / "L04" / "l04_raw.jsonl"
GOLDEN_PATH = HERE / "_shared" / "lab_golden.jsonl"
ART_OUT_DIR = HERE.parent.parent / "artifacts" / "L04"


def load_raw(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"✗ {path} 不存在 — 先跑 {('L03-minimal-rag/run_eval.py' if 'L03' in str(path) else 'L04-langchain-version/run.py')}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def doc_id_of(text: str) -> str | None:
    """按 text 反查 doc_id(语料里全文唯一)。"""
    for d in CORPUS_DOCS:
        if d["text"] == text:
            return d["doc_id"]
    return None


def score_row(raw: dict, gold: dict) -> dict:
    """对一道题算所有指标。"""
    retrieved = raw.get("retrieved_docs", [])
    answer = raw.get("answer", "")
    abstained = raw.get("abstained", False)
    must_abstain = gold.get("must_abstain", False)

    # 1) retrieval_recall_at_k:命中的 doc_id 是否覆盖 expected_doc_ids
    retrieved_ids = {doc_id_of(t) for t in retrieved} - {None}
    expected_ids = set(gold.get("expected_doc_ids", []))
    if not expected_ids:
        retrieval_recall = 1.0 if not retrieved_ids else 0.0  # unanswerable 题:不该取回任何
        retrieval_correct = (not retrieved_ids)
    else:
        retrieval_recall = (len(retrieved_ids & expected_ids) / len(expected_ids))
        retrieval_correct = (retrieved_ids & expected_ids) == expected_ids

    # 2) contains_expected
    if must_abstain:
        contains_expected = True  # 不查答案内容
    else:
        contains_expected = any(kw in answer for kw in gold.get("expected_answer_contains", []))

    # 3) abstention_correct
    if must_abstain:
        abstention_correct = abstained
    else:
        abstention_correct = not abstained

    # 4) fake-RAGAS 4 个 scorer
    ragas = RagasRow(
        question=gold["question"],
        answer=answer,
        contexts=retrieved,
        ground_truth=gold.get("expected_answer_contains", [""])[0] if gold.get("expected_answer_contains") else "",
    )
    ragas_scores = {name: round(fn(ragas), 4) for name, fn in SCORERS.items()}

    return {
        "id": gold["id"],
        "slice": gold["slice"],
        "must_abstain": must_abstain,
        "abstained": abstained,
        "retrieved_ids": sorted(retrieved_ids),
        "expected_ids": sorted(expected_ids),
        "retrieval_recall": retrieval_recall,
        "retrieval_correct": retrieval_correct,
        "contains_expected": contains_expected,
        "abstention_correct": abstention_correct,
        "ragas": ragas_scores,
        "latency_ms": raw.get("latency_ms", 0),
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    overall = {
        "n": n,
        "retrieval_recall_avg": round(sum(r["retrieval_recall"] for r in rows) / n, 4) if n else 0.0,
        "retrieval_correct_rate": round(sum(r["retrieval_correct"] for r in rows) / n, 4) if n else 0.0,
        "contains_expected_rate": round(sum(r["contains_expected"] for r in rows) / n, 4) if n else 0.0,
        "abstention_correct_rate": round(sum(r["abstention_correct"] for r in rows) / n, 4) if n else 0.0,
        "p50_latency_ms": round(sorted(r["latency_ms"] for r in rows)[n // 2], 1) if n else 0.0,
    }
    # RAGAS 平均
    for metric in SCORERS:
        overall[f"ragas_{metric}_avg"] = round(
            sum(r["ragas"][metric] for r in rows) / n, 4
        ) if n else 0.0

    by_slice: dict[str, dict] = {}
    for r in rows:
        s = r["slice"]
        by_slice.setdefault(s, []).append(r)
    by_slice_summary = {
        s: {
            "n": len(rs),
            "retrieval_recall_avg": round(sum(r["retrieval_recall"] for r in rs) / len(rs), 4),
            "contains_expected_rate": round(sum(r["contains_expected"] for r in rs) / len(rs), 4),
            "abstention_correct_rate": round(sum(r["abstention_correct"] for r in rs) / len(rs), 4),
        }
        for s, rs in by_slice.items()
    }
    return {"overall": overall, "by_slice": by_slice_summary}


def render_compare_md(l03: dict, l04: dict) -> str:
    lines = [
        "# L04 对照实验 — L03 (手写) vs L04 (LangChain LCEL)",
        "",
        "语料:8 篇闭域文档(见 `01-foundations/labs/_shared/lab_corpus.py`)。",
        "题库:8 题(`01-foundations/labs/_shared/lab_golden.jsonl`)。",
        "评估:`eval/ragas/run.py` 的 4 个 fake-RAGAS 指标 + 自定义 retrieval/answer/abstain/latency。",
        "",
        "## Overall(全部 8 题)",
        "",
        "| 指标 | L03 手写 | L04 LangChain | Δ |",
        "|---|---|---|---|",
    ]
    for k in l03["overall"]:
        v3, v4 = l03["overall"][k], l04["overall"][k]
        if isinstance(v3, float) and isinstance(v4, float):
            delta = round(v4 - v3, 4)
            lines.append(f"| `{k}` | {v3} | {v4} | {delta:+} |")
        else:
            lines.append(f"| `{k}` | {v3} | {v4} | — |")
    lines.append("")
    lines.append("## Per-slice")
    lines.append("")
    for s in sorted(set(l03["by_slice"]) | set(l04["by_slice"])):
        m3 = l03["by_slice"].get(s, {})
        m4 = l04["by_slice"].get(s, {})
        lines.append(f"### slice={s}")
        lines.append("")
        lines.append("| 指标 | L03 | L04 |")
        lines.append("|---|---|---|")
        for k in m3:
            if k == "n":
                continue
            lines.append(f"| {k} | {m3[k]} | {m4.get(k, '—')} |")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ fake-RAGAS:`eval/ragas/run.py` 的 4 个 scorer 是手写的简化版,")
    lines.append("> 不是真 `ragas` 包;只是用来给两个 pipeline 出可比指标。")
    lines.append("> 真要 ragas 包需 `pip install ragas` 并配 LLM judge,见 plan 范围外。")
    return "\n".join(lines) + "\n"


def main() -> None:
    golden = {g["id"]: g for g in load_golden()}
    l03_raw = {r["id"]: r for r in load_raw(ART_L03)}
    l04_raw = {r["id"]: r for r in load_raw(ART_L04)}

    l03_rows = [score_row(l03_raw[qid], golden[qid]) for qid in sorted(golden)]
    l04_rows = [score_row(l04_raw[qid], golden[qid]) for qid in sorted(golden)]

    l03_metrics = aggregate(l03_rows)
    l04_metrics = aggregate(l04_rows)

    out = {
        "corpus_size": len(CORPUS_DOCS),
        "n_questions": len(golden),
        "l03": {**l03_metrics, "rows": l03_rows},
        "l04": {**l04_metrics, "rows": l04_rows},
    }

    ART_OUT_DIR.mkdir(parents=True, exist_ok=True)
    eval_json = ART_OUT_DIR / "eval.json"
    eval_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    compare_md = ART_OUT_DIR / "compare.md"
    compare_md.write_text(render_compare_md(l03_metrics, l04_metrics), encoding="utf-8")

    print(f"✓ eval.json  → {eval_json}")
    print(f"✓ compare.md → {compare_md}")
    print()
    print("━━━ Overall ━━━")
    for k in l03_metrics["overall"]:
        v3 = l03_metrics["overall"][k]
        v4 = l04_metrics["overall"][k]
        print(f"  {k:35s}  L03={v3}  L04={v4}")


if __name__ == "__main__":
    main()