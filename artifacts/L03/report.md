# L03 — 最小 RAG 报告

- 嵌入后端：**ollama**
- 生成后端：**mmx**
- 链路：query → embed → cosine top-3 → context pack → mmx(MiniMax-M3)

## 与 LangChain 版的差异（L04）
- 不依赖 langchain，全部 < 100 行
- 检索效率 O(N)，生产环境需 HNSW
- 无 BM25、rerank、agent（后续 lab 补齐）
