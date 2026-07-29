"""L22 — pgvector 写路径：upsert / delete / reconcile / metadata 过滤。

教学目标：
1. 看到 **5 个核心方法**就是全部业务面（upsert / delete_by_source / reconcile_source / query / existing_chunk_ids）
2. 看到 HNSW 索引参数 `m=16 / ef_construction=64` 的物理意义
3. 看到 ACL 过滤 **下推到 SQL**（pgvector 相对 Chroma 的核心优势）
4. 看到 "reconcile_source 清僵尸向量" 的语义

生产实现：`projects/p6-ingestion-platform/src/store.py`

运行：
    python 05-data-engineering/labs/L22-pgvector-write/run.py

输出：
- artifacts/L22/sql_reference.md
"""
from __future__ import annotations

from pathlib import Path

ART = Path("artifacts/L22")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 5 个核心方法（接口契约） ────────────────────────
INTERFACE = """
class IndexStore(ABC):
    def upsert(self, records: list[VectorRecord]) -> UpsertResult: ...
    def delete_by_source(self, source_id: str) -> int: ...
    def reconcile_source(self, source_id: str, current_ids: list[str]) -> int: ...
    def query(self, embedding, k=5, where=None) -> list[dict]: ...
    def existing_chunk_ids(self, source_id: str) -> set[str]: ...
    def fetch_all(self, where=None) -> list[VectorRecord]: ...   # P1 懒加载
    def count(self) -> int: ...
"""

# ── 2. pgvector 真实 DDL（生产版见 P6 store.py: pg_ddl） ──────
DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id     TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(768) NOT NULL,        -- 768 = nomic-embed-text dim
    sensitivity  TEXT NOT NULL DEFAULT 'public',
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    doc_version  TEXT,
    content_hash TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ★ HNSW 索引（生产调参：m=16 / ef_construction=64）
CREATE INDEX IF NOT EXISTS rag_chunks_emb_hnsw
    ON rag_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- metadata JSONB 索引（让 ACL 过滤能下推到 SQL）
CREATE INDEX IF NOT EXISTS rag_chunks_metadata_gin
    ON rag_chunks
    USING GIN (metadata jsonb_path_ops);

CREATE INDEX IF NOT EXISTS rag_chunks_source_id_idx
    ON rag_chunks (source_id);
"""

# ── 3. 关键 SQL 操作（生产版见 P6 store.py: pg_upsert_sql / pg_reconcile_sql / pg_query_sql） ─
UPSERT = """
-- 幂等 upsert：相同 chunk_id 走 DO UPDATE
INSERT INTO rag_chunks (chunk_id, source_id, text, embedding, sensitivity, metadata)
VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb)
ON CONFLICT (chunk_id) DO UPDATE SET
    text        = EXCLUDED.text,
    embedding   = EXCLUDED.embedding,
    sensitivity = EXCLUDED.sensitivity,
    metadata    = EXCLUDED.metadata;
"""

RECONCILE = """
-- 清掉这篇文档里"应该不在"的旧 chunk（编辑后）
DELETE FROM rag_chunks
WHERE source_id = %s
  AND chunk_id != ALL(%s::text[]);   -- 不在 current_ids 的就是 stale
"""

QUERY = """
-- ANN 排名 + ACL 过滤下推到 SQL
SELECT chunk_id, text, metadata,
       1 - (embedding <=> %s::vector) AS similarity
FROM rag_chunks
WHERE sensitivity = ANY(%s)              -- ACL 过滤
ORDER BY embedding <=> %s::vector        -- ★ HNSW 走 ANN 索引
LIMIT %s;
"""


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L22 — pgvector 写路径")
    print("════════════════════════════════════════════\n")

    print("─── 5 个核心方法（业务面）───")
    print(INTERFACE)

    print("─── HNSW 索引参数 ───")
    print("m=16: 每个节点连 16 个邻居 → 越大越准但越慢/越占内存")
    print("ef_construction=64: 建图时每个节点的候选列表大小 → 越大建得越慢但图质量越好")
    print("生产默认: m=16, ef_construction=64（pgvector 默认）")
    print("查询时还可调: SET hnsw.ef_search = 100; （recall 优先，调高）")

    print("\n─── pgvector vs Chroma 对照 ───")
    print("|          | Chroma            | pgvector           |")
    print("| 写       | upsert()          | INSERT ... ON CONFLICT DO UPDATE |")
    print("| 过滤     | where={} 标量    | 任意 SQL WHERE，ACL 下推 |")
    print("| 与业务表 | 分离              | 同库可 JOIN、有事务 |")
    print("| ANN      | 默认              | HNSW / IVFFlat     |")

    print("\n─── ★ reconcile_source（清僵尸向量）───")
    print("chunk_id = sha256(source_id | text | ...) → 改一个字 = 新 id")
    print("光 upsert 删不掉旧的那条 → 不对账 = 永久僵尸向量，静默污染召回")
    print("P5 原 bug：collection.add() + skip-if-exists，正是这个症状")

    out = f"""# L22 — pgvector 写路径

## 5 个核心方法 = 全部业务面

```python
{INTERFACE}
```

## DDL

```sql
{DDL}
```

## 关键 SQL

### 幂等 upsert

```sql
{UPSERT}
```

### reconcile（清僵尸）

```sql
{RECONCILE}
```

### 查询 + ACL 下推

```sql
{QUERY}
```

## pgvector vs Chroma

| 维度 | Chroma | pgvector |
|---|---|---|
| 起步 | 零基建 | 需 Postgres |
| 写语义 | upsert() | INSERT ON CONFLICT |
| 过滤 | where 标量 | 任意 SQL WHERE（ACL 下推） |
| 与业务表 | 分离 | 同库可 JOIN |
| ANN | 默认 | HNSW / IVFFlat |

## ★ reconcile_source 必要性
`chunk_id = sha256(...)` → 改一个字就是新 id，光 upsert 删不掉旧的。
**不对账 = 永久僵尸向量，静默污染召回**。
这是 P5 原 `collection.add() + skip-if-exists` 的 bug。

## 生产版
`projects/p6-ingestion-platform/src/store.py`：
- `pg_ddl / pg_upsert_sql / pg_reconcile_sql / pg_query_sql / pg_fetch_all_sql` 都是纯函数
- 双后端：`PgVectorIndexStore` + `ChromaIndexStore`
- `open_index_store("auto")` 在没 P6_PG_DSN 时**大声**警告降级到 Chroma

## 面试题
1. HNSW 的 m=16 / ef_construction=64 是什么意义？调大会怎样？
2. pgvector 的 metadata JSONB 为什么要建 GIN 索引？
3. 为什么 `chunk_id` 进 PRIMARY KEY 而不是 BIGSERIAL？"""
    (ART / "sql_reference.md").write_text(out, encoding="utf-8")
    print(f"\n✓ 报告 → {ART / 'sql_reference.md'}")


if __name__ == "__main__":
    main()