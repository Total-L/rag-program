# RAG 实战训练营 — 4 阶段路线

> 配套 `00-NAVIGATION.md` 顶层文件。每阶段 5–6 个 lab + 项目作业 + 验收标准。

## 全局心法

> 三个"不"：**不只跑通**（要能讲清原理）、**不只学框架**（要会手写最小版本）、**不只看指标**（要能解释指标变化的原因）。

---

## 阶段 1 — Foundations（5 lab · 1 周）

**目标**：能白板讲清 RAG 全链路，2 个版本（手写 + LangChain）都能复现。

| Lab | 主题 | 关键产出 |
|---|---|---|
| L01 | NumPy 手写余弦检索 | `numpy_cosine.py`、可视化 |
| L02 | Chunking 三策略对比 | 对比表（chunk 数 / recall@10 / 重建率） |
| L03 | 最小 RAG（Ollama + mmx） | `minimal-rag/main.py` 端到端 |
| L04 | LangChain 复刻 | `langchain-version/main.py` 同样指标 |
| L05 | 手工指标（precision/recall/MRR） | 20 题小金标 + 评测脚本 |

**验收**：
- [ ] 能在 5 分钟内画出 RAG 流程图并解释每步
- [ ] L01–L05 全部 `make verify-foundations` 通过
- [ ] 一句话回答：Embedding 是什么，为什么不直接用 bag-of-words

---

## 阶段 2 — Retrieval Quality（5 lab · 1 周）

**目标**：能讲清 dense+sparse+rerank 是 2026 主流；量化 RAGAS 在金标上的提升。

| Lab | 主题 | 关键产出 |
|---|---|---|
| L06 | BM25（rank_bm25） | 与 dense 并排的 retrieval 评测 |
| L07 | RRF 融合 + 权重调参 | 融合对比表 |
| L08 | 交叉编码器重排 | recall@10 / nDCG@10 提升曲线 |
| L09 | 查询改写 4 策略 | multi-query / HyDE / step-back / decomposition A/B |
| L10 | Context packing | per-source cap / near-dup / token budget / "lost in the middle" |

**验收**：
- [ ] `make verify-retrieval` 全过
- [ ] RAGAS 报告生成：Faithfulness ≥ 0.80, Context Recall ≥ 0.75
- [ ] 能讲清：为什么 BM25 + dense 通常比纯 dense 好？为什么 rerank 后能提 5–15 个点？

---

## 阶段 3 — Advanced & Production（6 lab · 1.5 周）

**目标**：能讲清"如果上线，要解决什么"。

| Lab | 主题 | 关键产出 |
|---|---|---|
| L11 | Grounded prompting | JSON-mode 响应校验 + 拒答 |
| L12 | Agentic RAG（LangGraph） | tool use / Self-RAG / CRAG |
| L13 | GraphRAG | networkx 重建 `[[wikilinks]]` + 多跳评测 |
| L14 | Production | cost guard + trace 解析 + p50/p95（注：**incremental indexing 在阶段 5 的 L18/L22 才真正实现**） |
| L15 | Security | prompt injection / 路径穿越 / 分类排除 / URI 注入 |
| L16 | Streamlit 三面板 | Chat / Sources / Diagnostics |

**验收**：
- [ ] `make verify-advanced` 全过
- [ ] L12 在 multihop 切片上比单跳 RAG 高 5+ 点
- [ ] L15 单元测试覆盖 5 类攻击
- [ ] L16 Streamlit 可启可聊可看到 sources

---

## 阶段 4 — Interview & Resume（4 步 · 0.5 周）

**目标**：能不带稿回答 50 题，每题能链接到自己的项目证据。

| 步 | 主题 | 关键产出 |
|---|---|---|
| L17 | STAR 故事 | `interview/star-stories.md`（3 个故事） |
| L18 | 50 面试题 | `interview/questions.md`（theory 25 + production 25） |
| L19 | Mock 脚本 | `interview/mock-script.md` + `make mock-interview` |
| L20 | 1 页简历 | `interview/resume.md`（中英）+ 1 分钟电梯演讲 |

**验收**：
- [ ] 50 题能默答
- [ ] 每题 30 秒能答完 + 30 秒能展开
- [ ] 简历能说清 3 个项目的技术栈 / 你的角色 / 量化结果

---

## 全程交付（简历包装）

### ⭐ P1 — Enterprise KB Assistant（深度主项目）
- **栈**：LangChain + LangGraph + Chroma + BM25 + Cross-Encoder + Ollama(llama3.1) + MiniMax-M3
- **数据**：合成 200 份企业内文档
- **亮点**：混合检索 + 重排 + 引用溯源 + 拒答 + ACL + Streamlit
- **指标**：50 题金标 recall@10 ≥ 0.85, Faithfulness ≥ 0.85, Citation Validity = 100%
- **交付**：架构图、ADR、postmortem、README

### P2 — Code & Tech Docs RAG（轻量 1）
- 仓库级索引 + AST 切分 + 符号搜索

### P3 — Customer Service & Tickets（轻量 2）
- 意图路由 + 多轮 + 工单生成 + 转人工

### P4 — Financial Research & Announcements（轻量 3）
- 表格解析 + 时间过滤 + 引用溯源

---

## 时间盒（参考）

| 节奏 | 4 阶段总时长 | 适合 |
|---|---|---|
| 6 周强化 | 1 / 1 / 2 / 2 周 | 已有 LLM 基础 + 时间紧 |
| 10–12 周系统（推荐） | 2 / 2 / 4 / 2 周 | 边工作边学 |
| 无固定周期 | 按能力关卡推进 | 业余时间 |

**建议**：以能力关卡为进度度量，而不是以周数。每个 lab 完成后做"教别人 5 分钟"自测。
