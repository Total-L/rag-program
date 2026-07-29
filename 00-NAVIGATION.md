# rag-program — 工作区导航

> 企业级 RAG 实战项目。两层结构：企业程序本体（自带即可跑）+ course-mirror 附加层（可选，依赖训练营 kbchat）。

## 0. 5 分钟导览

| 你想做什么 | 去看哪里 |
|---|---|
| **安装 + 跑企业程序** | [`README.md`](./README.md) → `make verify-enterprise` |
| 启用训练营配套 lab | `bash scripts/install-course-mirror.sh` → `make verify-course-mirror` |
| 看 P1 旗舰（简历首选） | [`projects/p1-enterprise-kb/`](./projects/p1-enterprise-kb/) |
| 看 P6 摄取平台 | [`projects/p6-ingestion-platform/`](./projects/p6-ingestion-platform/) |
| 看 Stage-5 数据工程 | [`05-data-engineering/`](./05-data-engineering/) |
| 50 道面试题 | [`interview/questions.md`](./interview/questions.md) |
| 1 页简历 | [`interview/resume.md`](./interview/resume.md) |
| 跑评测 | `make eval-ragas` / `make regression` |
| 端到端验证 | `make verify-all` |

---

## 1. 工作区拓扑

```
rag_program/                       ← 本工作区（独立可运行）
├── .venv/                          ← 默认本地 venv（不是 /rag-course-venv）
├── projects/p1-p6/                 ← 企业程序（不依赖 rag-course）
├── 05-data-engineering/            ← 数据工程（不依赖 rag-course）
├── eval/  interview/  datasets/    ← 自有
├── course-mirror/labs/             ← 可选附加层（需要 rag-course 存在）
│     01-foundations/  02-retrieval-quality/  03-advanced-production/
└── scripts/  Makefile  pyproject.toml
```

**关键设计（ADR-0005）**：
- 企业程序本体（`projects/*`、`05-data-engineering/*`）**不 import kbchat**，自包含运行。
- 16 个 lab 移至 `course-mirror/labs/` 作为可选附加层；跑它们需要 `pip install -e ".[course-mirror]"`（要求 `/Users/totallai/rag-course` 存在）。
- 默认 venv = `./.venv`（可被 `RAG_PROG_VENV` 覆盖）。

---

## 2. 目录速查

```
rag_program/
├── 00-NAVIGATION.md            ← 本文件
├── ROADMAP.md                  ← 4 阶段路线
├── Makefile                    ← verify-enterprise / verify-course-mirror / eval
├── pyproject.toml              ← 自有依赖 + [course-mirror] optional extra
├── .env.example                ← 环境变量
├── README.md
│
├── projects/
│   ├── p1-enterprise-kb/       ⭐ 深度主项目（简历首选）
│   ├── p2-code-docs-rag/       轻量 1
│   ├── p3-customer-service/    轻量 2
│   ├── p4-finance-research/    轻量 3
│   ├── p5-multi-format-loader/ 轻量 4
│   └── p6-ingestion-platform/  ⭐ 旗舰（Stage-5 数据工程生产形态）
│
├── 05-data-engineering/        ← 6 教学 lab (L17-L22) + docker-compose
│
├── datasets/
│   ├── generators/             ← 4 套合成数据脚本
│   ├── golden/                 ← 50 题金标（与 kbchat 字段兼容，但内容自有）
│   ├── public/                 ← HotpotQA / NQ 抽样
│   └── fixtures/               ← 中间产物（含 P2 默认 sample_corpus）
│
├── eval/
│   ├── ragas/                  ← RAGAS 评测 harness（自实现 4 个简化打分器）
│   └── regression/             ← CI 阈值脚本 + thresholds.yaml
│
├── interview/                  ← 50 题 + STAR + 简历 + mock runner
│
├── course-mirror/              ← 训练营配套 lab（可选附加层，依赖 kbchat）
│   ├── README.md
│   └── labs/
│       ├── 01-foundations/         (L01-L05)
│       ├── 02-retrieval-quality/   (L06-L10)
│       └── 03-advanced-production/ (L11-L16)
│
├── examples/                   ← 最小 demo（无 kbchat 依赖）
│
└── scripts/                    ← env-check / install / install-course-mirror / self_test
```

---

## 3. 双模型后端约定

| 用途 | 默认 | 备选 |
|---|---|---|
| Embedding | Ollama `nomic-embed-text` | — |
| 通用 LLM | `mmx` CLI (MiniMax-M3) | Ollama `llama3.1` |

切换：环境变量 `RAG_PROG_LLM_BACKEND=ollama|mmx`（详见 `.env.example`）。

---

## 4. 验证矩阵（两层）

| 命令 | 验证目标 | 是否需要 kbchat |
|---|---|---|
| `make env-check` | Python / Ollama / mmx / 工作区结构 | ❌ |
| `make install` | 装企业程序本体到 .venv | ❌ |
| `make install-course-mirror` | 装 course-mirror 附加层 | ✅ |
| `make verify-enterprise` | P1–P6 + Stage-5 + eval | ❌ |
| `make verify-course-mirror` | course-mirror/labs 16 lab | ✅ |
| `make verify-p1` | P1 主项目 50 题金标 | ❌ |
| `make verify-p2-4` | 4 个轻量项目 smoke | ❌ |
| `make mock-interview` | 50 题抽样自测评分 | ❌ |
| `make regression` | CI 阈值门 | ❌ |
| `make verify-all` | 上面全部 | ✅（含 course-mirror） |

---

## 5. 解耦决策（与训练营的关系）

- **企业程序本体**：不复制 kbchat 源码，自实现 markdown heading 切分、context packing、Ollama 客户端、path ACL（详见 P1 `src/_compat.py` + `docs/adr/0005-p1-kbchat-free.md`）。
- **course-mirror 附加层**：与 `rag-course` 训练营主线 lab 1:1 对照；**故意保留**对 kbchat 的依赖。
- **金标集共用 schema**：`datasets/golden/golden_v1.jsonl` 与训练营 golden set 字段兼容（QuestionSlice 枚举对齐），但内容自有、可独立维护。

---

## 6. 不在本工作区范围

- 重写 kbchat（course-mirror 用其作为参考实现）
- 编排（Airflow/Prefect DAG）、IaC、密钥管理、GDPR/DSR、漂移检测、成本账本
- 真实云 / SaaS 凭证接入（连接器接口已预留 `PROD_*` 开关）
- 28 个教学 notebook（训练营职责）
