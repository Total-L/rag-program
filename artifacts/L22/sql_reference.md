# L22 — pgvector 写路径

## 5 个核心方法 = 全部业务面

```python

class IndexStore(ABC):
    def upsert(self, records: list[VectorRecord]) -> UpsertResult: ...
    def delete_by_source(self, source_id: str) -> int: ...
    def reconcile_source(self, source_id: str, current_ids: list[str]) -> int: ...
    def query(self, embedding, k=5, where=None) -> list[dict]: ...
    def existing_chunk_ids(self, source_id: str) -> set[str]: ...
    def fetch_all(self, where=None) -> list[VectorRecord]: ...   # P1 懒加载
    def count(self) -> int: ...

```

## DDL

```sql

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

```

## 关键 SQL

### 幂等 upsert

```sql

-- 幂等 upsert：相同 chunk_id 走 DO UPDATE
INSERT INTO rag_chunks (chunk_id, source_id, text, embedding, sensitivity, metadata)
VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb)
ON CONFLICT (chunk_id) DO UPDATE SET
    text        = EXCLUDED.text,
    embedding   = EXCLUDED.embedding,
    sensitivity = EXCLUDED.sensitivity,
    metadata    = EXCLUDED.metadata;

```

### reconcile（清僵尸）

```sql

-- 清掉这篇文档里"应该不在"的旧 chunk（编辑后）
DELETE FROM rag_chunks
WHERE source_id = %s
  AND chunk_id != ALL(%s::text[]);   -- 不在 current_ids 的就是 stale

```

### 查询 + ACL 下推

```sql

-- ANN 排名 + ACL 过滤下推到 SQL
SELECT chunk_id, text, metadata,
       1 - (embedding <=> %s::vector) AS similarity
FROM rag_chunks
WHERE sensitivity = ANY(%s)              -- ACL 过滤
ORDER BY embedding <=> %s::vector        -- ★ HNSW 走 ANN 索引
LIMIT %s;

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
3. 为什么 `chunk_id` 进 PRIMARY KEY 而不是 BIGSERIAL？