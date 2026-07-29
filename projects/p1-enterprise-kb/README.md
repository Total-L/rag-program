# P1 — Enterprise KB Assistant（深度主项目）

> **简历首选**。4 周 RAG 实战训练的集大成作品。

## 亮点（面试讲故事用）

1. **混合检索**：BM25 + dense + RRF 融合（k=60）
2. **重排序**：cross-encoder / heuristic 二选一
3. **引用溯源**：每条回答带 `[1][2]` 引用，可点 `obsidian://` 跳转
4. **拒答通道**：abstain 阈值 + JSON-mode 校验
5. **ACL 三档**：public / internal / restricted，配合 `sensitivity` 过滤
6. **Streamlit 三面板**：Chat / Sources / Diagnostics
7. **金标评测**：50 题 RAGAS 报告 + 7 个 slice
8. **可观测性**：JSONL trace + p50/p95 + 错误率

## 技术栈

| 层 | 选型 |
|---|---|
| Embedding | Ollama `nomic-embed-text` |
| LLM | mmx `MiniMax-M3` / Ollama `llama3.1:8b` |
| 向量库 | Chroma（cosine HNSW） |
| 稀疏 | rank_bm25（持久化 pickle） |
| 重排 | cross-encoder / HeuristicReranker |
| 评测 | RAGAS（faithfulness / answer_relevancy / context_precision/recall） |
| UI | Streamlit |
| 框架 | LangChain + LangGraph + 自研编排 |

## 目录

```
projects/p1-enterprise-kb/
├── README.md
├── configs/                # ACL / 阈值 / 模型选择
├── data/
│   ├── eval/              # 50 题金标 + RAGAS 输入
│   └── acl/               # 权限配置
├── src/
│   ├── config.py
│   ├── acl.py
│   ├── pipeline.py        # 端到端 RAG pipeline
│   ├── app.py             # Streamlit UI
│   ├── eval.py            # 50 题评测
│   └── smoke.py           # 烟雾测试
├── tests/
│   ├── test_acl.py
│   ├── test_pipeline.py
│   ├── test_security.py
│   ├── test_golden.py
│   └── run_eval.py        # 评测入口
└── docs/
    ├── architecture.md
    ├── adr/
    │   ├── 0001-hybrid-retrieval.md
    │   ├── 0002-cross-encoder-rerank.md
    │   └── 0003-acl-sensitivity.md
    ├── postmortem.md
    └── api.md
```

## 跑

```bash
# 1. 准备数据
python -m datasets.generators.enterprise --out datasets/fixtures/enterprise

# 2. 跑评测
make verify-p1
```

## 关键指标（目标 vs 当前）

| 指标 | 目标 | 评测命令 |
|---|---|---|
| recall@10 | ≥ 0.85 | `make verify-p1` |
| MRR | ≥ 0.70 | 同上 |
| Faithfulness | ≥ 0.85 | RAGAS 报告 |
| Context Recall | ≥ 0.80 | RAGAS 报告 |
| Citation Validity | 100% | `tests/test_pipeline.py` |
| Abstention | ≥ 0.80 | `tests/test_golden.py` |
| 敏感路径泄露 | 0 | `tests/test_acl.py` |
