"""A/B pipeline harness(ENTERPRISE_AUDIT C1)。

用法:
    python -m eval.ragas.compare --compare cfg_a.yaml cfg_b.yaml

两份 yaml 是 pipeline config 差异(见 `pipeline_config.py` 字段),
跑同一个 golden v1 出 side-by-side 指标 + delta。

输出:
- eval/reports/ab_<ts>.json(完整结构化)
- 控制台 markdown 表格

例 cfg yaml 形态(可同时给 part):
    chunk_size: 400
    chunk_overlap: 60
    top_k: 8
    reranker: cross-encoder
    hybrid_alpha: 0.5

替换 rule:任何 cfg_a/cfg_b 都可直接覆盖 P1Pipeline 构造时传入的字段;
未提供的字段走现有默认。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml


@dataclass
class CompareReport:
    a_label: str
    b_label: str
    n: int
    metrics_a: dict
    metrics_b: dict
    delta: dict  # metric -> {a, b, delta (b-a), better: 'a'|'b'|'tie'}
    timestamp: str
    notes: str = ""


def _load_pipeline_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"pipeline config not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _run_pipeline_against_golden(config: dict, n: int = 50):
    """用指定 config 跑 P1 pipeline,n 题金标,返回(rows + overall)。

    抽象:实际拉起 pipeline 的细节,后面再收敛。
    """
    try:
        from projects.p1_enterprise_kb.src.acl import User  # type: ignore
        from projects.p1_enterprise_kb.src.eval import evaluate  # type: ignore
        from projects.p1_enterprise_kb.src.pipeline import P1Pipeline  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "P1 pipeline 不可导入;compare.py 仅支持 P1"
        ) from e

    golden_path = Path("datasets/golden/golden_v1.jsonl")
    golden = [
        json.loads(line)
        for line in golden_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = golden[:n]
    p = P1Pipeline(
        user=User("alice", "executive"), **(config or {})
    )
    p.index_corpus()
    return evaluate(p, golden)


def compare(cfg_a_path: Path, cfg_b_path: Path, n: int = 50) -> CompareReport:
    cfg_a = _load_pipeline_config(cfg_a_path)
    cfg_b = _load_pipeline_config(cfg_b_path)

    rep_a = _run_pipeline_against_golden(cfg_a, n=n)
    rep_b = _run_pipeline_against_golden(cfg_b, n=n)

    metrics_a: dict = rep_a.get("overall", {})
    metrics_b: dict = rep_b.get("overall", {})

    keys = set(metrics_a) | set(metrics_b)
    delta: dict = {}
    higher_is_better = {
        "citation_validity",
        "abstention_correctness",
        "contains_expected",
        "hit_rate",
        "recall_at_10",
        "mrr",
        "ndcg_at_5",
        "ndcg_at_10",
    }
    lower_is_better = {
        "p50_latency_ms",
        "hallucination_rate",
        "unsupported_claims_rate",
        "numeric_hallucination_rate",
        "protected_path_leakage",
    }
    for k in keys:
        a = metrics_a.get(k)
        b = metrics_b.get(k)
        if a is None or b is None:
            continue
        d = round(b - a, 4)
        if k in higher_is_better:
            winner = "b" if b > a else ("a" if a > b else "tie")
        elif k in lower_is_better:
            winner = "b" if b < a else ("a" if a < b else "tie")
        else:
            winner = "tie"  # 未约定方向(>= vs <=)
        delta[k] = {"a": a, "b": b, "delta": d, "better": winner}

    return CompareReport(
        a_label=str(cfg_a_path),
        b_label=str(cfg_b_path),
        n=n,
        metrics_a=metrics_a,
        metrics_b=metrics_b,
        delta=delta,
        timestamp=datetime.now(UTC).isoformat(),
    )


def _render_markdown(report: CompareReport) -> str:
    lines = [f"# A/B Pipeline Compare — {report.n} 题", ""]
    lines.append(f"- A: `{report.a_label}`")
    lines.append(f"- B: `{report.b_label}`")
    lines.append(f"- 时间: {report.timestamp}")
    lines.append("")
    lines.append("| metric | A | B | Δ (B-A) | better |")
    lines.append("|---|---:|---:|---:|:--:|")
    for k, v in sorted(report.delta.items()):
        lines.append(
            f"| {k} | {v['a']} | {v['b']} | {v['delta']:+.4f} | {v['better']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B pipeline harness")
    parser.add_argument("--compare", nargs=2, required=True, metavar=("CFG_A", "CFG_B"))
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--out-dir", default="eval/reports")
    args = parser.parse_args()

    a_path = Path(args.compare[0])
    b_path = Path(args.compare[1])

    report = compare(a_path, b_path, n=args.n)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"ab_{ts}.json"
    json_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = _render_markdown(report)
    print(md)
    print(f"\n✓ JSON → {json_path}")


if __name__ == "__main__":
    main()
