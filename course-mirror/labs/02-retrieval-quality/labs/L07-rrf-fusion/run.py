"""L07 — RRF（Reciprocal Rank Fusion）融合 + 权重调参。

跑：
    python 02-retrieval-quality/labs/L07-rrf-fusion/run.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ART = Path("artifacts/L07")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. RRF 实现 ─────────────────────────────────────────────
def rrf(rankings: list[list[tuple[str, float]]], k: int = 60,
        weights: list[float] | None = None) -> list[tuple[str, float]]:
    """多个 ranked list → 融合。

    rankings[i] 是 [(doc_id, score), ...] 已按 score 降序。
    weights[i] 给每个 list 一个权重。
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict[str, float] = {}
    for w, r in zip(weights, rankings):
        for rank, (doc_id, _) in enumerate(r, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


# ── 2. 玩具数据：两个 retriever 的结果 ─────────────────────
DOCS = [f"D{i}" for i in range(10)]

# 假设 dense 排前 5
dense = [(f"D{i}", 0.9 - 0.05 * i) for i in [3, 5, 1, 7, 0]]
# BM25 排前 5
bm25 = [(f"D{i}", 10.0 - 1.0 * i) for i in [0, 3, 2, 8, 5]]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L07 — RRF 融合 + 权重调参")
    print("════════════════════════════════════════════\n")

    print("dense top5:", [d for d, _ in dense])
    print("bm25  top5:", [d for d, _ in bm25])

    # 3 组权重
    configs = [
        ("equal", [1.0, 1.0]),
        ("bm25 偏重", [0.4, 1.0]),
        ("dense 偏重", [1.0, 0.4]),
        ("k=10 (敏感)", [1.0, 1.0]),
    ]
    results = []
    for name, w in configs:
        k = 10 if "k=10" in name else 60
        fused = rrf([dense, bm25], k=k, weights=w[:2])
        top5 = [d for d, _ in fused[:5]]
        print(f"\n[{name:15s}] top5: {top5}")
        results.append({"config": name, "weights": w, "k": k, "top5": top5})

    # 与 kbchat 库对拍
    try:
        from kbchat.retrieval.hybrid import reciprocal_rank_fusion
        fused = reciprocal_rank_fusion([dense, bm25], weights=[1.0, 1.0])
        print(f"\nkbchat.reciprocal_rank_fusion: top5 = {[d for d,_ in fused[:5]]}")
    except Exception as e:
        print(f"\n⚠ kbchat 不可用：{e}")

    # 写报告
    (ART / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "report.md").write_text(
        "# L07 — RRF 融合报告\n\n"
        "## 公式\n"
        "RRF(d) = Σ 1/(k + rank_i(d))，k 常取 60\n\n"
        "## 观察\n"
        "- 在两个 retriever 都命中的 doc，融合后分数叠加 → 排前\n"
        "- 只有一个命中的 doc，分数弱 → 排后\n"
        "- 权重偏置可调，但一般 equal 起点\n\n"
        "## 面试要点\n"
        "1. 为什么 RRF 而非线性融合（concat, max）？\n"
        "2. RRF vs 加权倒数排名 vs CombSUM / CombMNZ？\n"
        "3. 怎么知道 BM25 和 dense 哪个更重要？→ 调权重 + 评测\n",
        encoding="utf-8",
    )
    print(f"\n✓ L07 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
