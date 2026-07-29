# L22 — pgvector 写路径

> "怎么把数据写进向量数据库"的生产答案。

## 学习目标
- 看到 **5 个核心方法**就是全部业务面
- 看到 HNSW 索引参数 `m=16 / ef_construction=64` 的物理意义
- 看到 ACL 过滤**下推到 SQL**（pgvector 相对 Chroma 的核心优势）
- 看到 "reconcile_source 清僵尸向量" 的语义

## 跑

```bash
make lab-L22
# 或
python 05-data-engineering/labs/L22-pgvector-write/run.py
```

## 关键代码点
- 5 个核心方法：`upsert / delete_by_source / reconcile_source / query / existing_chunk_ids`
- `pg_ddl` —— HNSW 索引参数 + JSONB 索引
- `pg_upsert_sql` —— `INSERT ... ON CONFLICT DO UPDATE`
- `pg_reconcile_sql` —— 清掉 source_id 下不在 current_ids 的 chunk
- `pg_query_sql` —— ACL 过滤下推到 SQL

## pgvector vs Chroma 对照

| | Chroma | pgvector |
|---|---|---|
| 起步 | 零基建 | 需 Postgres |
| 写 | `upsert()` | `INSERT ON CONFLICT DO UPDATE` |
| 过滤 | `where={}` 标量 | 任意 SQL WHERE（**ACL 下推**） |
| 与业务表 | 分离 | 同库可 JOIN |
| ANN | 默认 | HNSW / IVFFlat |

## ★ reconcile_source 必要性
`chunk_id = sha256(source_id | text | ...)` → 改一个字 = 新 id。
光 upsert 删不掉旧的 → **不对账 = 永久僵尸向量，静默污染召回**。
这是 P5 原 `collection.add() + skip-if-exists` 的 bug。

## 输出
- `artifacts/L22/sql_reference.md` —— DDL + 关键 SQL + 对照表

## 面试题
1. HNSW 的 m=16 / ef_construction=64 是什么意义？调大会怎样？
2. pgvector 的 metadata JSONB 为什么要建 GIN 索引？
3. 为什么 `chunk_id` 进 PRIMARY KEY 而不是 BIGSERIAL？