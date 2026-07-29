"""P1 Enterprise KB Assistant — 配置加载。

自实现（详见 ADR-0005）：P1 不再继承 kbchat.config，
只用纯 dataclass + 环境变量覆盖。涵盖 ACL 敏感度、RAGAS 评测路径、P1 特有路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# parents[0]=projects, parents[1]=rag_program, parents[2]=/Users/totallai
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]


@dataclass
class P1Config:
    """P1 特有配置。"""

    # 数据
    corpus_dir: Path = field(
        default_factory=lambda: WORKSPACE_ROOT / "datasets/fixtures/enterprise"
    )
    golden_path: Path = field(
        default_factory=lambda: WORKSPACE_ROOT / "datasets/golden/golden_v1.jsonl"
    )
    eval_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data/eval")
    # 检索
    chunk_size: int = 400
    chunk_overlap: int = 60
    top_k_dense: int = 8
    top_k_sparse: int = 8
    top_k_final: int = 5
    rrf_k: int = 60
    # 评测
    ragas_metrics: list[str] = field(
        default_factory=lambda: [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ]
    )
    # 阈值
    abstain_threshold: float = 0.3

    @classmethod
    def from_env(cls) -> P1Config:
        return cls(
            chunk_size=int(os.environ.get("P1_CHUNK_SIZE", "400")),
            chunk_overlap=int(os.environ.get("P1_CHUNK_OVERLAP", "60")),
            top_k_dense=int(os.environ.get("P1_TOP_K_DENSE", "8")),
            top_k_sparse=int(os.environ.get("P1_TOP_K_SPARSE", "8")),
            top_k_final=int(os.environ.get("P1_TOP_K_FINAL", "5")),
            rrf_k=int(os.environ.get("P1_RRF_K", "60")),
        )


def get_p1_config() -> P1Config:
    cfg = P1Config.from_env()
    cfg.eval_dir.mkdir(parents=True, exist_ok=True)
    return cfg
