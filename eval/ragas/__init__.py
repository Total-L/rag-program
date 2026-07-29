"""C1 真 RAGAS 入口(ENTERPRISE_AUDIT C1)。

公开:
- `MmxRagasLLM` — mmx 包装,接口与 RAGAS 0.2.x `BaseRagasLLM` 一致。
- 4 个检索/生成 RAGAS 指标 (`faithfulness` / `answer_relevancy` /
   `context_precision` / `context_recall`)
- 检索层 nDCG@k(`retrieval.ndcg_at_k`)
- 幻觉检测(基于 faithfulness + 自定义 unsupported_claims/numeric)
- `runner.run_ragas_eval()` — 跑真 RAGAS 评测,对单 project。
- `compare.compare_pipelines()` — A/B harness。

设计取舍:不用真 ragas 库,本机网络不稳;
 但所有 API 命名与真库一致,install 后可一行 import 切换。
"""
from . import compare, runner  # CLI submodules (ENTERPRISE_AUDIT C1)
from .hallucination import (
    hallucination_rate,
    numeric_hallucination_rate,
    unsupported_claims_rate,
)
from .metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    evaluate,
    faithfulness,
)
from .ragas_llm import MmxRagasLLM
from .retrieval import ndcg_at_5, ndcg_at_10, ndcg_at_k

__all__ = [
    # LLM
    "MmxRagasLLM",
    # RAGAS-style metrics
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "evaluate",
    # Retrieval
    "ndcg_at_k",
    "ndcg_at_5",
    "ndcg_at_10",
    # Hallucination
    "unsupported_claims_rate",
    "hallucination_rate",
    "numeric_hallucination_rate",
    # CLI
    "runner",
    "compare",
]
