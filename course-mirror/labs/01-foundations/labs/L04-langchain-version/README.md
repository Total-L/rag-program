# L04 — LangChain 复刻最小 RAG

## 学习目标
- 看到 LangChain 的"组件拼装"优势
- LCEL `|` 算子的可读性
- 切 LLM/Embed 仅一行改动

## 跑
```bash
make lab-L04
```

## 与 L03 关键对比
| 维度 | L03 手写 | L04 LangChain |
|---|---|---|
| 代码行数 | ~120 | ~60 |
| 检索 | 自定义 cosine | `Chroma.as_retriever` |
| 提示 | 字符串拼接 | `ChatPromptTemplate` |
| Chain | 显式调用 | LCEL `|` 算子 |
| 依赖 | Ollama + mmx | + langchain 17 个子包 |

## 教学要点
- LangChain 解决的不是"能不能跑"，而是"组件拼装 + 可观测性 + 可替换性"
- 框架不神秘——L03 你已见过每一步
- 生产中常：LangChain 起手 + 关键模块手写（如 rerank、retrieval）
