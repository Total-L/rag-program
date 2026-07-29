"""P6 — 企业级摄取平台（Enterprise Ingestion Platform）。

回答三个问题的可运行答案：
① 怎么提取企业级数据  → `connectors/`（SQL / 对象存储 / Confluence / 文件系统）
② 怎么优化数据        → `clean.py`（归一化+近重复）、`pii.py`（脱敏）、`chunkers.py`（分块+增强）
③ 怎么把数据写进向量库 → `store.py`（upsert / delete_by_source / reconcile，pgvector + Chroma 双后端）
整个流程                → `run_ingest.py`（含 manifest 增量 + DLQ）
"""

from __future__ import annotations

from .manifest import Delta, ManifestStore
from .models import SENSITIVITY_LEVEL, Chunk, IngestFailure, Sensitivity, SourceRecord
from .store import (
    ChromaIndexStore,
    IndexStore,
    PgVectorIndexStore,
    UpsertResult,
    VectorRecord,
    open_index_store,
)

__all__ = [
    "SourceRecord",
    "Chunk",
    "IngestFailure",
    "Sensitivity",
    "SENSITIVITY_LEVEL",
    "ManifestStore",
    "Delta",
    "IndexStore",
    "ChromaIndexStore",
    "PgVectorIndexStore",
    "VectorRecord",
    "UpsertResult",
    "open_index_store",
]
