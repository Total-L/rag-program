# P1 — 架构图与组件说明

## 端到端流程

```
┌──────────────────────────────────────────────────────────────┐
│  User (with role)                                             │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   ACL 过滤         │  ← role + sensitivity
                │   public/internal │     (3 档)
                │   /restricted     │
                └───────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Index Build      │  ← chunking + embedding
                │  (MarkdownHead    │     (Ollama / BGE)
                │   + embed)        │
                └───────────────────┘
                          │
                          ▼  Ask(question)
                ┌───────────────────┐
                │   Hybrid Retrieve │  ← dense (cosine)
                │                   │     + BM25
                │                   │     → RRF(k=60)
                └───────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Rerank          │  ← CrossEncoder /
                │                   │     HeuristicReranker
                └───────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Context Pack    │  ← per-source cap=2
                │                   │     token budget=2400
                │                   │     "lost in middle" 排序
                └───────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Grounded        │  ← JSON-mode
                │   Generation      │     system prompt 禁幻觉
                │   (mmx / ollama)  │     引用 [E1] [E2]
                └───────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Validate        │  ← 引用必须在 valid set
                │   + Abstain       │     否则拒答
                └───────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  P1Result(answer, citations, abstained, latency_ms)          │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   Streamlit       │  ← Chat / Sources /
                │   三面板           │     Diagnostics
                └───────────────────┘
```

## 关键决策（ADR）

| ADR | 主题 |
|---|---|
| [0001](./adr/0001-hybrid-retrieval.md) | 混合检索 BM25 + dense + RRF |
| [0002](./adr/0002-cross-encoder-rerank.md) | Cross-encoder 重排 |
| [0003](./adr/0003-acl-sensitivity.md) | ACL 三档敏感度 |

## 自研 vs 复用（P1 自包含，ADR-0005）

| 组件 | 自研 | 复用第三方 | 理由 |
|---|---|---|---|
| chunking | **P1 自研** (`_compat.MarkdownHeadingSplitter`) | — | 不依赖 rag-course |
| embedding | **P1 自研** (`_compat.ollama_embed_*`) | Ollama HTTP | 不依赖 BGE 备选 |
| BM25 | — | `rank_bm25` | 行业标准库 |
| RRF | **P1 自实现** | — | ~10 行代码 |
| Rerank | **P1 自实现** (cross-encoder) | — | P1 主路径 |
| Context Pack | **P1 自研** (`_compat.pack_evidence`) | tiktoken | token budget 策略 |
| Generation | **P1 自实现** | Ollama / mmx | grounded prompt |
| Validation | **P1 自实现** | — | 引用校验、拒答 |
| **ACL** | **P1 自研** | — | 三档敏感度 + path allowlist |
| **持久化 + 增量** | **P1 自研** (P1IndexStore) | Chroma / pgvector | ADR-0004 |
| **P1Pipeline 编排** | | **P1 自研** | 业务流控制 |
| **Streamlit UI** | | **P1 自研** | 三面板设计 |
| **50 题金标** | | **P1 自研** | 业务评测集 |
