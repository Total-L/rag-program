"""P1 评测:50 题金标 + 7 slice 聚合。

ENTERPRISE_AUDIT C1 加入:
- nDCG@5 / nDCG@10:真检索排序质量指标(per-row,可重)
- hallucination_rate:基于 RAGAS faithfulness 的反指标(aggregate)

既存指标(hit_rate / recall_at_10 / mrr / citation_validity /
abstention_correctness / contains_expected)一字不动,threshold gate
(check_thresholds.py)无需改动。

设计取舍:
- nDCG 是 per-row(便宜,纯 stdlib)
- hallucination 是 aggregate-only(LLM judge,跑 50 次太贵,改成 1 次
   拉所有 rows + contexts 给 mmx 一次性 judge;生产环境可设采样率)。
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from acl import User
from pipeline import P1Pipeline

# C1: 真 RAGAS 指标(项目根 eval/ragas/)
_RAGAS_PATH = Path(__file__).resolve().parents[3] / "eval" / "ragas"
if str(_RAGAS_PATH.parent) not in sys.path:
    sys.path.insert(0, str(_RAGAS_PATH.parent))
try:
    from eval.ragas import (  # type: ignore
        MmxRagasLLM,  # type: ignore
        hallucination_rate,
        ndcg_at_5,
        ndcg_at_10,
    )
    _HAS_RAGAS = True
except Exception:  # pragma: no cover - 评测 CI 路径安全降级
    _HAS_RAGAS = False


def load_golden(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]  # noqa: E741


def evaluate(
    pipeline: P1Pipeline,
    golden: list[dict],
    *,
    compute_hallucination: bool = False,
    tenant_id: str | None = None,
) -> dict:
    """对金标集逐题跑,统计:recall@k, MRR, nDCG, citation validity, abstention correctness, faithfulness proxy。

    Args:
        pipeline: P1Pipeline 实例。
        golden: 黄金集行。
        compute_hallucination: 是否跑 LLM-judged hallucination_rate
                              (默认关 — 50 条 × LLM 开销大)。
        tenant_id: 显式传入；None 时从 tenant_context 读。
                  S2 后 ask() 强制需要 tenant；调用方可走 with tenant_scope() 隐式注入,
                  也可显式传 tenant_id。
    """
    if tenant_id is None:
        try:
            from tenant_context import get_current_tenant

            t = get_current_tenant()
            tenant_id = t.tenant_id if t else None
        except Exception:
            tenant_id = None
        if tenant_id is None:
            # 兜底:S2 多租户重构前 evaluate() 是「无 tenant」语义,回归测试
            # 仍按旧契约跑 → 直接用字符串 "default"。MagicMock pipeline 不关心,
            # 真实 pipeline.ask() 走 tenant_registry 找不到时也只警告不阻塞。
            tenant_id = "default"

    rows = []
    by_slice = defaultdict(list)
    for q in golden:
        r = pipeline.ask(q["question"], tenant_id=tenant_id)
        # citation validity:citations 必须非空 OR abstained=True
        citation_valid = (len(r.citations) > 0) or r.abstained
        # abstention correctness
        if q.get("must_abstain"):
            abst_correct = r.abstained
        else:
            abst_correct = not r.abstained
        # faithfulness proxy:answer 中是否含 expected_answer_contains 中至少 1 个
        ans = r.answer or ""
        contains_expected = (
            any(kw in ans for kw in q.get("expected_answer_contains", []))
            if not q.get("must_abstain")
            else True
        )
        # 真 hit_rate(命中):retrieved_source_ids ∩ expected_doc_ids 非空
        # 详见 RCA_p1_eval_regression.md 根因 1 修复(v2)。
        # 旧实现有 bug:startswith("hr-") 配 "E1" 永远 0;
        # 弱代理:bool(r.citations) and not r.abstained。
        # 当前:P1Result.retrieved_source_ids 暴露了真 source_id,
        # 可做精确 doc_id 集合比对。
        expected = set(q.get("expected_doc_ids", []))
        retrieved = r.retrieved_source_ids or []
        hit_set = expected & set(retrieved)
        hit = bool(hit_set)
        # 真 recall@k:k=topk 命中比例(topk 数量与 retrieved_source_ids 等长)
        recall_at_10 = round(len(hit_set) / max(len(expected), 1), 3) if expected else 0.0
        # 真 MRR:首个 expected_doc_ids 命中的排名倒数
        mrr = 0.0
        if expected:
            for i, sid in enumerate(retrieved, 1):
                if sid in expected:
                    mrr = round(1.0 / i, 3)
                    break
        # C1: nDCG@k(纯 stdlib)
        if _HAS_RAGAS:
            n5 = ndcg_at_5(retrieved, list(expected))
            n10 = ndcg_at_10(retrieved, list(expected))
        else:
            n5, n10 = 0.0, 0.0

        row = {
            "id": q["id"],
            "slice": q["slice"],
            "must_abstain": q.get("must_abstain", False),
            "abstained": r.abstained,
            "abstention_correct": abst_correct,
            "citation_valid": citation_valid,
            "contains_expected": contains_expected,
            "hit": hit,
            "recall_at_10": recall_at_10,
            "mrr": mrr,
            "ndcg_at_5": n5,
            "ndcg_at_10": n10,
            "latency_ms": r.latency_ms,
        }
        rows.append(row)
        by_slice[q["slice"]].append(row)

    # 聚合
    n = max(len(rows), 1)
    overall = {
        "n": len(rows),
        "citation_validity": round(sum(r["citation_valid"] for r in rows) / n, 3),
        "abstention_correctness": round(sum(r["abstention_correct"] for r in rows) / n, 3),
        "contains_expected": round(sum(r["contains_expected"] for r in rows) / n, 3),
        "hit_rate": round(sum(r["hit"] for r in rows) / n, 3),
        "recall_at_10": round(sum(r["recall_at_10"] for r in rows) / n, 3),
        "mrr": round(sum(r["mrr"] for r in rows) / n, 3),
        "ndcg_at_5": round(sum(r["ndcg_at_5"] for r in rows) / n, 4),
        "ndcg_at_10": round(sum(r["ndcg_at_10"] for r in rows) / n, 4),
        "p50_latency_ms": round(sorted(r["latency_ms"] for r in rows)[len(rows) // 2], 1),
    }

    # C1: hallucination_rate(LLM-judged aggregate, opt-in)
    if compute_hallucination and _HAS_RAGAS:
        try:
            llm = MmxRagasLLM()
            # 单独 pass:重跑每题拿 (answer, contexts) 给 LLM judge
            _answers: list[str] = []
            _contexts: list[str] = []
            for q in golden:
                rr = pipeline.ask(q["question"], tenant_id=tenant_id)
                _answers.append(rr.answer or "")
                _contexts.append(
                    "\n---\n".join(e.text for e in (rr.evidence or []))
                )
            overall["hallucination_rate"] = hallucination_rate(
                "\n\n".join(_answers), _contexts, llm
            )
        except Exception as e:
            overall["hallucination_rate"] = 0.0
            overall["hallucination_error"] = str(e)

    by_slice_summary = {
        s: {
            "n": len(rs),
            "abstention_correct": round(sum(r["abstention_correct"] for r in rs) / len(rs), 3),
            "contains_expected": round(sum(r["contains_expected"] for r in rs) / len(rs), 3),
            "ndcg_at_5": round(sum(r["ndcg_at_5"] for r in rs) / len(rs), 4),
        }
        for s, rs in by_slice.items()
    }
    return {"overall": overall, "by_slice": by_slice_summary, "rows": rows}


def main() -> None:
    print("════════════════════════════════════════════")
    print("  P1 — 50 题金标评测 (C1 真 RAGAS)")
    print("════════════════════════════════════════════\n")

    # S2 多租户:index_corpus() / ask() 都要求 tenant 在 context。
    # 命令行入口包一层 tenant_scope('default'),不污染 evaluate() 内部逻辑。
    from tenant import Tenant  # noqa: E402 — 局部 import 保持顶部清爽
    from tenant_context import tenant_scope  # noqa: E402

    default_tenant = Tenant.from_plan(tenant_id="acme-corp", name="Acme Corp (demo)")
    # C1 默认关 LLM judge(off by default for cost) — 在 with 外算,with 内只读
    compute_h = os.environ.get("P1_EVAL_HALLUCINATION", "0") == "1"
    golden = load_golden(Path("datasets/golden/golden_v1.jsonl"))
    print(f"→ 金标题数: {len(golden)}")
    if not golden:
        print("⚠ 无金标,跑:python -m datasets.golden.build_v1")
        return

    with tenant_scope(default_tenant):
        user = User(user_id="alice", role="executive")  # 全可见
        p = P1Pipeline(user=user)
        p.index_corpus()
        print(f"→ 索引 chunks: {len(p.index)}")

        report = evaluate(p, golden, compute_hallucination=compute_h)

    print("\n━━━ 整体 ━━━")
    for k, v in report["overall"].items():
        print(f"  {k}: {v}")
    print("\n━━━ 按 slice ━━━")
    for s, m in report["by_slice"].items():
        print(
            f"  {s:15s} n={m['n']:3d}  abst_correct={m['abstention_correct']:.2f}  contains={m['contains_expected']:.2f}  ndcg@5={m['ndcg_at_5']:.3f}"
        )

    out = Path("eval/reports/p1_golden_latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 评测报告 → {out}")


if __name__ == "__main__":
    main()
