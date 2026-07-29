# L04 — LangChain 复刻报告

## 与 L03（手写）对比

| 维度 | L03 手写 | L04 LangChain |
|---|---|---|
| 代码行数 | ~120 | ~60 |
| 检索 | 自定义 cosine | `Chroma.as_retriever` |
| 提示 | 字符串拼接 | `ChatPromptTemplate` |
| Chain | 显式调用 | LCEL `|` 算子 |
| 切换 LLM | 改 subprocess | 改 `Ollama(model=...)` |
| 流式输出 | 自己写 | `.stream()` |
| Token 计数 | 自己 | `CallbackHandler` |
| 依赖 | Ollama + mmx | + langchain 17 个子包 |

## 学到什么
- LangChain 解决的不是'能不能跑'，而是'组件拼装 + 可观测性 + 可替换性'
- 框架不神秘——L03 你已见过每一步
- 生产中常：LangChain 起手 + 关键模块手写（如 rerank、retrieval）
