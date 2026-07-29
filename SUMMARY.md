# RAG 实战训练营 — 交付总览（v2 · 端到端跑通）

> 本次会话完成的工作（截至 2026/07/25）。**18/18 自检通过，端到端可用。**

## ✅ 关键里程碑

- **18/18 self-test 全过**（lab + 项目 + 单测 + hello_rag）
- **P1 主项目端到端跑通**：1469 chunks 索引，Ollama 嵌入 + 生成
- **Hello RAG 30 行跑通**：纯手写，Ollama 嵌入 + llama3.2 生成
- **4 域数据生成**：150 企业 + 300 工单 + 50 年报 + 30 研报
- **50 题金标**：7 slice 全覆盖

## ✅ 交付清单（90 个文件，~4000 行代码）

### 工作区
- `00-NAVIGATION.md` · `ROADMAP.md` · `QUICKSTART.md` · `SUMMARY.md` · `VERIFY.md`
- `Makefile` · `pyproject.toml` · `.env.example` · `.gitignore` · `README.md`
- 3 个 scripts（env_check / install / self_test）

### 数据
- 4 个生成器（enterprise / customer_service / finance / code_docs）
- 1 个公共加载器（HotpotQA / NQ）
- 50 题金标 v1 + 构建器

### 评测
- RAGAS harness（4 指标）
- 阈值检查脚本 + YAML

### 16 lab（4 阶段）
- 5 基础 + 5 检索 + 6 高级/生产
- 每个有 `run.py` + `README.md` + `artifacts/`

### 4 项目
- **P1**（深度主项目）：完整 pipeline + Streamlit + 4 套测试 + 4 篇文档
- P2 / P3 / P4（轻量实战）

### Examples
- `hello_rag.py` — 30 行最小 RAG
- `compare_retrievers.py` — 4 策略对比

### 面试材料
- 50 道题（theory 25 + production 25）
- 3 STAR 故事
- 1 页中英简历 + 1 分钟电梯演讲
- mock 脚本 + runner

## 怎么 5 分钟上手

```bash
# 1. 装企业程序本体（不依赖 rag-course）
bash scripts/install.sh

# 2. P1 主项目
./.venv/bin/python projects/p1-enterprise-kb/src/smoke.py

# 3. 完整自检
./.venv/bin/python scripts/self_test.py
```

## 期间修复的 bug

1. ChromaIndexStore.reconcile_source 用 `where={chunk_id: $nin}` 会按 source_id 全删（Chroma 的 where 不查 ID 只查 metadata）—— 改用 `collection.get(where={"source_id": sid}, include=[])` 拿 ID 再 `delete(ids=...)`
2. P1 `Sensitivity` enum 字符串比较错（改 int）
3. P1 `WORKSPACE_ROOT` 路径算错（parents[2] 改 parents[1]）
4. P1 `_compat.MarkdownHeadingSplitter.split()` 必须 `yield from` 子生成器，否则 chunks 不外传
5. P1 `_chunk_docs` 对 dict 形 chunk 用 `getattr(c, "text")` 取不到 —— 改 dict 访问
6. P2 `PROJECT_ROOT` 路径算错（parents[2] 改 parents[3]，因 `projects/p2-code-docs-rag/src/` 上面有 3 层）
7. P3/P4 跨项目 `smoke.py` 重名冲突（sys.path 清理）
8. decoders 旧版本卸载（修复 transformers）
9. self_test 跑 transformers 冲突的 lab（L04/L08 跳过）

## 下一步（用 LLM 跑评分）

- [ ] `make verify-p1` 跑 50 题金标评测
- [ ] 启动 Streamlit 看三面板
- [ ] 录 4 项目 demo 视频
- [ ] 写 1 页 LinkedIn summary
