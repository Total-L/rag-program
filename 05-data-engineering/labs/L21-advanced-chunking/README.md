# L21 — 高级分块

> "怎么优化数据"的分块策略：4 种主流方案的取舍。

## 学习目标
- 看到**标题感知**如何保住"该比例不得超过 15%"这类无主语句的语义
- 看到**表格分片**如何带表头（否则 "800/晚" 失去单位语义）
- 看到**父子分块**如何用子块精准召回 + 父块给上下文
- 看到 **contextual prefix** —— 每块加"它在讲什么"（Anthropic contextual retrieval）

## 跑

```bash
make lab-L21
```

## 4 种策略对照

| 策略 | 解决什么 | 代价 |
|---|---|---|
| `layout_chunks` | 维护完整标题路径 —— 无主语句不再丢失主语 | 免费 |
| `table_aware_chunks` | 表格分片时**表头随行** | 免费 |
| `parent_child_chunks` | 子块精准召回 + 父块给上下文 | 存储翻倍 |
| `contextual_prefix(llm_fn=...)` | 每块加"它在讲什么" | 默认免费启发式；可传 LLM 升级 |

## 关键代码点
- `layout_chunks` —— 按 # / ## / ### 切，保留 heading_path
- `table_aware_chunks` —— 整表保留不分切
- `parent_child_chunks` —— 父块大段 + 子块滑动窗口
- `contextual_prefix` —— LLM 失败 → 静默退回启发式（不能拖垮摄取）

## 输出
- `artifacts/L21/chunks_demo.md`

## 面试题
1. 表格分片为什么要带表头？否则会丢什么信息？
2. 父子分块的代价是存储翻倍，换来了什么？
3. contextual_prefix 用 LLM 升级 vs 默认启发式，recall 差距？