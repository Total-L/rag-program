# L02 — Chunking 对比

| strategy | n | avg_size | max_size | min_size | coverage |
|---|---|---|---|---|---|
| fixed | 2 | 182.5 | 200 | 165 | 1.09 |
| recursive | 8 | 38.4 | 66 | 9 | 0.916 |
| markdown | 7 | 37.7 | 60 | 15 | 0.788 |

## 经验法则
- **MD 文档** → MarkdownHeading（保 heading_path 利于过滤）
- **聊天/邮件** → Recursive（按段落 / 句号 fallback）
- **代码/固定 schema** → 按行/函数切，**不要用 Fixed**
- **chunk_size** 经验：中文 200–400 字，英文 200–500 token
- **overlap** 经验：10–20% chunk_size
