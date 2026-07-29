# Stage 5 — 数据工程（企业级 RAG 三件套）

> "怎么提取企业级数据 / 怎么优化数据 / 怎么把数据写进向量数据库" 的教学 + 生产代码。

## 一句话定位
原仓库的 RAG 算法已经很扎实（混合检索 / 重排 / Agentic / 评测门），
但**数据工程这一层**此前只有 `glob("*.md")` + 立即读全文。
Stage 5 把这一层补成生产形态。

## 三个层次

| 层 | 教学校 | 生产代码 | 状态 |
|---|---|---|---|
| **① 提取** | `L17-connectors/` `L18-incremental-cdc/` | `projects/p6-ingestion-platform/src/connectors/` `src/manifest.py` | ✅ |
| **② 优化** | `L19-clean-normalize/` `L20-pii-redaction/` `L21-advanced-chunking/` | `src/clean.py` `src/pii.py` `src/chunkers.py` | ✅ |
| **③ 写向量库** | `L22-pgvector-write/` | `src/store.py`（pgvector + Chroma 双后端） | ✅ |
| **整个流程** | — | `projects/p6-ingestion-platform/run_ingest.py` | ✅ |
| **P1 旗舰落库** | — | `projects/p1-enterprise-kb/src/index_store.py` + ADR-0004 | ✅ |

## 6 个 lab 怎么跑

```bash
make lab-L17   # 企业数据源连接器 + provenance 信封
make lab-L18   # 增量 / CDC（ManifestStore + delta）
make lab-L19   # 清洗 / 归一化 / 近重复（手写 MinHash）
make lab-L20   # PII / 密钥脱敏（9 类正则 + 三道防线）
make lab-L21   # 高级分块（layout / table / parent-child / contextual）
make lab-L22   # pgvector 写路径（HNSW / upsert / reconcile / metadata 过滤）

make verify-data-engineering  # 6 个 lab 一把跑
make verify-p6                # P6 摄取平台单测（无 pgvector 也能跑）
make verify-p6-pg             # P6 pgvector 端到端（需先 docker compose up postgres）
```

## 本地基座（可选，本机有 Docker 时）

```bash
make data-platform-up    # 起 Postgres+pgvector + MinIO
make data-platform-down  # 关

export P6_PG_DSN='postgresql://rag:rag@localhost:5432/ragkb'
python projects/p6-ingestion-platform/run_ingest.py --backend pgvector --full-scan
pytest projects/p6-ingestion-platform/tests --run-pg
```

## 教学顺序建议
1. **L17 → L22**：每 lab 自包含 30~60 分钟
2. lab 之间通过 P6 生产代码互相关联 —— README 里都标了"看生产版"
3. 最后读 P6 README 把"端到端流程"过一遍

## 不在本阶段范围（记为后续"生产运维"）
编排(Airflow/Prefect DAG)、容器化部署与 IaC、密钥管理与环境晋升、
GDPR/保留/DSR、漂移检测、成本账本、告警、零停机蓝绿重建。