# rag-program — 企业级 RAG 实战项目

> 这是**独立可运行的企业级 RAG 项目**：
> P1–P6 旗舰 + Stage-5 数据工程 + 评测门 + 面试材料。
> 与 `rag-course` 训练营是兄弟项目但**互不耦合**——`pip install -e .` 即可跑。

## 快速开始（5 分钟）

```bash
# 1. 准备环境（一次性，本机不依赖 rag-course）
bash scripts/install.sh        # 装企业程序本体到 .venv
make env-check                 # 验证 Ollama / Python 包 / 工作区结构

# 2. 一键跑通企业程序
make verify-enterprise         # P1–P6 + Stage-5 全跑

# 3. （可选）启用训练营配套 lab
bash scripts/install-course-mirror.sh
make verify-course-mirror      # 跑 course-mirror/labs/ 16 个 lab
```

## 范围分层

| 层 | 路径 | 依赖 | 何时跑 |
|---|---|---|---|
| **企业程序本体** | `projects/p1-p6/`、`05-data-engineering/`、`eval/`、`interview/`、`datasets/` | 仅 pip install 自身 | `make verify-enterprise` |
| **course-mirror 附加层** | `course-mirror/labs/{01..03}/` | `bash scripts/install-course-mirror.sh`（依赖 `/Users/totallai/rag-course` 存在；脚本内部 `pip install kbchat @ file:///...`,不走 pyproject extra — F-8 2026-07-31） | `make verify-course-mirror` |
| **示例 + 工具** | `examples/`、`scripts/`、`Makefile` | 自身 | `make help` |

**企业程序本体可以**：
- 在没有 `/rag-course` 的机器上 `pip install -e . && make verify-enterprise` 全绿
- 自包含所有需要的工具（P1 内部有 `_compat.py` 自实现 markdown heading 切分、Ollama 嵌入、context pack、path ACL，详见 `projects/p1-enterprise-kb/docs/adr/0005-p1-kbchat-free.md`）
- 不被训练营上游 API 漂移无声影响

**course-mirror 附加层**：
- 是与 `rag-course` 训练营主线 1:1 对照的教学 lab 子模块
- **故意保留**对 `kbchat` 的依赖 —— 这是设计选择，让读者可来回对照
- 用法见 [`course-mirror/README.md`](./course-mirror/README.md)

## 4 个核心项目（企业程序本体）

| # | 名称 | 类型 | 用途 |
|---|---|---|---|
| P1 | Enterprise KB Assistant | ⭐ 旗舰 | 简历首选：混合检索 + 重排 + 引用 + ACL + 持久化 + 增量 |
| P2 | Code & Tech Docs RAG | 轻量 | 仓库级代码检索（AST 切分） |
| P3 | Customer Service & Tickets | 轻量 | 意图路由 + 多轮对话 |
| P4 | Financial Research | 轻量 | 表格 + 时间敏感 |
| P5 | Multi-format Loader | 轻量 | PDF/DOCX/XLSX/HTML 多格式入库 |
| P6 | Ingestion Platform | 旗舰 | 企业摄取：连接器 / CDC / 脱敏 / 分块 / 双后端向量库 |

详见 [`00-NAVIGATION.md`](./00-NAVIGATION.md)。

## 解耦决策（ADR）

- `projects/p1-enterprise-kb/docs/adr/0005-p1-kbchat-free.md` — P1 自实现 4 处原 kbchat 接口的取舍
- 完整 ADR 列表在 `projects/p1-enterprise-kb/docs/adr/`

## 评测门

```bash
make verify-enterprise    # 全部企业程序
make regression           # 阈值门（eval/regression/thresholds.yaml）
```

阈值包括：recall@10 ≥ 0.85、citation_validity = 1.00、protected_path_leakage = 0、Stage-5 的 ingestion_idempotency = 1.00、PII 泄漏 = 0、upsert 幂等 = 1.00、DLQ 失败率 ≤ 1%、吞吐 ≥ 50 chunks/s。

## 生产 pgvector 部署（B 端到端）

P6 `PgVectorIndexStore` 是生产声明后端（Chroma 只作本地原型）。SQL 生成器都有
单测覆盖，但**真实数据库的 upsert / query / tenant pushdown / fetch_all /
delete_by_source / reconcile 端到端**需要连真 PG。

### 起容器（dev/staging）

```bash
# Docker Desktop / OrbStack / colima 任一可用,本项目已含 compose
docker compose -f 05-data-engineering/docker-compose.yml up -d postgres
# 上面的 compose 用 pgvector/pgvector:pg16 镜像，含 vector 扩展。

# macOS native 也行: 装 Postgres.app,在 psql 里:
# CREATE EXTENSION IF NOT EXISTS vector;
```

默认 DSN: `postgresql://rag:rag@localhost:5432/rag`

### 跑端到端测试

```bash
cd projects/p6-ingestion-platform
P6_PG_DSN=postgresql://rag:rag@localhost:5432/rag \
  pytest tests/test_pgvector_e2e.py -v --run-pg
# 应 8 passed：建表、upsert、idempotent、query topk、tenant pushdown、
# fetch_all、delete_by_source、reconcile。

# 不传 --run-pg 会全部 SKIP（CI 友好，与 P6 既有单测约定一致）。
# 容器跑了但想重启: docker compose -f 05-data-engineering/docker-compose.yml restart postgres
```

### K8S / 生产部署
详见 `docs/K8S_DEPLOY.md` 和 `deploy/helm/rag-program/values.yaml` 的 `postgres` 段。
