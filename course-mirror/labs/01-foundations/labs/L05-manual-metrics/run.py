"""L05 — 手工指标实现：precision@k, recall@k, MRR, nDCG@k。

跑：
    python 01-foundations/labs/L05-manual-metrics/run.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ART = Path("artifacts/L05")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 指标实现 ────────────────────────────────────────────
def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """retrieved[:k] 中相关文档的比例。"""
    if k == 0:
        return 0.0
    return sum(1 for r in retrieved[:k] if r in relevant) / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return sum(1 for r in retrieved[:k] if r in relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for i, r in enumerate(retrieved, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def mrr(queries: list[tuple[list[str], set[str]]]) -> float:
    return sum(reciprocal_rank(r, rel) for r, rel in queries) / max(len(queries), 1)


def dcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    return sum(
        (1.0 / math.log2(i + 2) if r in relevant else 0.0)
        for i, r in enumerate(retrieved[:k])
    )


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    ideal = sorted([1.0] * min(len(relevant), k), reverse=True)
    ideal_dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal))
    if ideal_dcg == 0:
        return 0.0
    return dcg_at_k(retrieved, relevant, k) / ideal_dcg


# ── 2. 玩具金标（8 文档 / 5 问题）────────────────────────
DOCS = [f"D{i}" for i in range(8)]
GOLDEN = [
    # (query, retrieved by some retriever, relevant set)
    ("Q1: 年假", ["D0", "D3", "D5"], {"D0", "D4"}),     # D0 命中，D4 漏
    ("Q2: P0", ["D5", "D1", "D0"], {"D5"}),
    ("Q3: 差旅", ["D2", "D7", "D1"], {"D2", "D6"}),
    ("Q4: VPN", ["D3", "D5", "D6"], {"D3"}),
    ("Q5: vest", ["D4", "D0", "D2"], {"D4"}),
]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L05 — 手工检索指标")
    print("════════════════════════════════════════════\n")

    k = 3
    rows = []
    for q, retr, rel in GOLDEN:
        p = precision_at_k(retr, rel, k)
        r = recall_at_k(retr, rel, k)
        rr = reciprocal_rank(retr, rel)
        n = ndcg_at_k(retr, rel, k)
        print(f"{q:12s}  P@{k}={p:.3f}  R@{k}={r:.3f}  RR={rr:.3f}  nDCG@{k}={n:.3f}")
        rows.append({"q": q, "p@3": p, "r@3": r, "rr": rr, "ndcg@3": n})

    m = mrr([(r, rel) for _, r, rel in GOLDEN])
    print(f"\nMRR (5 q): {m:.3f}")

    # 与 kbchat 库对拍
    try:
        from kbchat.evaluation.metrics import (
            precision_at_k as kp, recall_at_k as kr, reciprocal_rank as krr,
            mrr as kmrr, ndcg_at_k as kn,
        )
        # kbchat 接口可能不同，这里仅示意
        print("✓ kbchat.evaluation.metrics 存在，签名可能略不同；做接口适配时再对拍")
    except Exception as e:
        print(f"⚠ kbchat 不可用：{e}")

    (ART / "metrics.json").write_text(
        json.dumps({"k": k, "per_query": rows, "mrr": round(m, 3)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ART / "report.md").write_text(
        "# L05 — 手工指标报告\n\n"
        "## 公式速记\n"
        "- **Precision@k** = |retrieved[:k] ∩ relevant| / k\n"
        "- **Recall@k** = |retrieved[:k] ∩ relevant| / |relevant|\n"
        "- **MRR** = 平均 1/rank_of_first_relevant\n"
        "- **nDCG@k** = DCG@k / IDCG@k（考虑位置权重）\n\n"
        "## 何时用什么\n"
        "- **Precision@k**：关心前 k 准不准（如搜索排序）\n"
        "- **Recall@k**：关心召回够不够（如 RAG 检索）\n"
        "- **MRR**：单答案场景（FAQ）\n"
        "- **nDCG@k**：多相关 + 排序敏感\n",
        encoding="utf-8",
    )
    print(f"\n✓ L05 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
