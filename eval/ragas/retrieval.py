"""检索层指标(纯 stdlib,不依赖 LLM)。

ENTERPRISE_AUDIT C1 — nDCG@k。

为什么独立文件:
- nDCG@k 是真检索指标,在 retrieval 阶段计算,
  不需要 LLM judge,所以从 metrics.py(LLM-judged)拆出。
- 跟黄金集 `expected_doc_ids` 对齐即可。

二值 graded-relevance: 命中 expected_doc_ids 的 doc relevance=1,其余 0。
如未来需要分级(2/1/0),改 `relevance` 函数即可。
"""
from __future__ import annotations

import math


def _relevance(doc_id: str, expected: set[str]) -> int:
    """二值 relevance:在 expected 集合里 = 1,否则 0。"""
    return 1 if doc_id in expected else 0


def ndcg_at_k(
    retrieved_source_ids: list[str] | None,
    expected_doc_ids: list[str] | None,
    *,
    k: int = 5,
) -> float:
    """归一化折损累计增益(@k)。

    公式:
        DCG@k   = sum_{i=1..k} rel_i / log2(i+1)
        IDCG@k  = sum_{i=1..min(k,|relevant|)} 1 / log2(i+1)
        nDCG@k  = DCG@k / IDCG@k

    边界:
    - retrieved 为空 / expected 为空 → 0.0(没信号)
    - 全部 top-k 都命中 → 1.0
    - 没命中任何一个 expected → 0.0
    """
    if not retrieved_source_ids or not expected_doc_ids:
        return 0.0

    expected_set = set(expected_doc_ids)
    # 取 top-k
    top_k = list(retrieved_source_ids)[:k]

    # DCG
    dcg = 0.0
    for i, did in enumerate(top_k, 1):
        rel = _relevance(did, expected_set)
        if rel:
            dcg += rel / math.log2(i + 1)

    # IDCG:理想排序是把所有 expected doc 放在最前
    n_relevant = min(k, len(expected_set))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_relevant + 1))

    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def ndcg_at_5(retrieved, expected) -> float:
    return ndcg_at_k(retrieved, expected, k=5)


def ndcg_at_10(retrieved, expected) -> float:
    return ndcg_at_k(retrieved, expected, k=10)


__all__ = ["ndcg_at_k", "ndcg_at_5", "ndcg_at_10"]
