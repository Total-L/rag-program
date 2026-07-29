# L03 — 最小 RAG（Ollama + mmx）

## 学习目标
- 看到 RAG 全链路 ≤ 100 行
- 体验无 LangChain 的"裸"实现
- 理解后端切换：嵌入 / 生成 都可替换

## 跑
```bash
make lab-L03
```

## 后端优先级
| 阶段 | 嵌入 | 生成 |
|---|---|---|
| 默认 | Ollama `nomic-embed-text` | mmx `MiniMax-M3` |
| 回退 1 | kbchat BGE 本地 | Ollama `llama3.1:8b` |

切换：`RAG_PROG_EMBED_BACKEND=ollama|bge`，`RAG_PROG_LLM_BACKEND=mmx|ollama`

## 链路
```
query → embed → cosine top-3 → context pack → mmx → answer
```

## 面试要点
1. 为什么 cosine 而不是 L2 / 点积？
2. context pack 的 token 上限如何设？（影响：失忆 vs 成本）
3. 系统 prompt 为何要"仅根据上下文回答"？否则幻觉如何量化？
