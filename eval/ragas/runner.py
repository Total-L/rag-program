"""C1 真 RAGAS runner(ENTERPRISE_AUDIT C1)。

替代 `eval/ragas/run.py`(手写打分器),使用 RAGAS-协议兼容的真实指标。

用法:
    python -m eval.ragas.runner --project p1-enterprise-kb
    python -m eval.ragas.runner --all --metrics faithfulness,answer_relevancy

输出:eval/reports/ragas_<project>_<ts>.json + 控制台打印聚合分

输入数据契约:
    projects/<proj>/data/eval/ragas_input.jsonl
    每行:{"question":..., "answer":..., "contexts":[...], "ground_truth":... (可选)}

如果没有 ragas_input.jsonl,会自动尝试调用 P1 pipeline 构造,见 _build_rows_from_p1()。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)
from .ragas_llm import MmxRagasLLM

SUPPORTED_METRICS = {
    "faithfulness": "LLM judge:答案是否基于 context",
    "answer_relevancy": "LLM judge:答案切题程度(0..5 → 归一)",
    "context_precision": "LLM judge:context 是否对 ground_truth 有用",
    "context_recall": "LLM judge:ground_truth 拆 statements 是否被 context 覆盖",
}


@dataclass
class RagasRow:
    question: str
    answer: str
    contexts: list[str]
    ground_truth: str | None = None


@dataclass
class RagasReport:
    project: str
    timestamp: str
    n: int
    metrics: dict
    by_slice: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    backend: str = "MmxRagasLLM(ragas-API-compatible shim)"


def _score(row: RagasRow, metric: str, llm: MmxRagasLLM) -> float:
    if metric == "faithfulness":
        return faithfulness(row.answer, row.contexts, llm)
    if metric == "answer_relevancy":
        return answer_relevancy(row.question, row.answer, row.contexts, llm)
    if metric == "context_precision":
        if not row.ground_truth:
            return 0.0
        return context_precision(row.question, row.contexts, row.ground_truth, llm)
    if metric == "context_recall":
        if not row.ground_truth:
            return 0.0
        return context_recall(row.ground_truth, row.contexts, llm)
    raise ValueError(f"unknown metric: {metric}")


def evaluate(
    rows: list[RagasRow],
    metrics: list[str],
    llm: MmxRagasLLM,
) -> dict[str, float]:
    """对全部 row 跑指标,返回聚合(0..1)。

    失败 strict:LLM 调用失败 → 0.0(不静默 0.5)。
    """
    out: dict[str, list[float]] = {m: [] for m in metrics}
    for row in rows:
        for m in metrics:
            try:
                out[m].append(_score(row, m, llm))
            except Exception:
                out[m].append(0.0)
    return {m: round(sum(v) / len(v), 4) if v else 0.0 for m, v in out.items() if v}


def load_ragas_rows(path: Path) -> list[RagasRow]:
    if not path.exists():
        return []
    rows: list[RagasRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        rows.append(RagasRow(**d))
    return rows


def _build_rows_from_p1(n: int = 5) -> list[RagasRow]:
    """无 ragas_input.jsonl 时,从 P1 pipeline 拉 n 条做 mini 评测。"""
    try:
        sys.path.insert(0, ".")
        from projects.p1_enterprise_kb.src.acl import User  # type: ignore
        from projects.p1_enterprise_kb.src.pipeline import P1Pipeline  # type: ignore
    except Exception as e:
        print(f"⚠ 无法导入 P1 pipeline: {e}")
        return []
    golden_path = Path("datasets/golden/golden_v1.jsonl")
    if not golden_path.exists():
        print(f"⚠ 黄金集不存在: {golden_path}")
        return []
    golden = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden = golden[:n]
    p = P1Pipeline(user=User("alice", "executive"))
    p.index_corpus()
    rows: list[RagasRow] = []
    for q in golden:
        r = p.ask(q["question"])
        ctxs = [e.text for e in r.evidence] if hasattr(r, "evidence") else []
        rows.append(
            RagasRow(
                question=q["question"],
                answer=r.answer or "",
                contexts=ctxs,
                ground_truth=" ".join(q.get("expected_answer_contains", [])),
            )
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="C1 真 RAGAS runner")
    parser.add_argument("--project", default="p1-enterprise-kb")
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--metrics",
        default=",".join(SUPPORTED_METRICS.keys()),
        help=f"逗号分隔,可选: {','.join(SUPPORTED_METRICS.keys())}",
    )
    parser.add_argument("--input", help="ragas_input.jsonl 路径")
    parser.add_argument("--n", type=int, default=5, help="无 input 时跑 mini-n 题")
    parser.add_argument("--out-dir", default="eval/reports")
    args = parser.parse_args()

    metrics_list = [m.strip() for m in args.metrics.split(",") if m.strip()]
    for m in metrics_list:
        if m not in SUPPORTED_METRICS:
            print(f"✗ 不支持的指标: {m};可选: {list(SUPPORTED_METRICS.keys())}")
            sys.exit(2)

    projects = (
        ["p1-enterprise-kb", "p2-code-docs-rag", "p3-customer-service", "p4-finance-research"]
        if args.all
        else [args.project]
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = MmxRagasLLM()

    for proj in projects:
        rows: list[RagasRow] = []
        if args.input:
            rows = load_ragas_rows(Path(args.input))
        elif proj == "p1-enterprise-kb":
            input_path = Path(f"projects/{proj}/data/eval/ragas_input.jsonl")
            if input_path.exists():
                rows = load_ragas_rows(input_path)
            else:
                rows = _build_rows_from_p1(n=args.n)

        if not rows:
            print(f"⚠ {proj}: 无数据,跳过")
            continue

        scores = evaluate(rows, metrics_list, llm)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report = RagasReport(
            project=proj,
            timestamp=datetime.now(UTC).isoformat(),
            n=len(rows),
            metrics=scores,
        )
        out_path = out_dir / f"ragas_{proj}_{ts}.json"
        out_path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"✓ {proj}: n={len(rows)} metrics={scores} → {out_path}")


if __name__ == "__main__":
    main()
