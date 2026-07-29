# L02 — Chunking 三策略对比

## 学习目标
- 看到 fixed / recursive / markdown 在同一份文档上的差异
- 看到 chunk 数、长度分布、覆盖率
- 复用 `kbchat.chunking` 做交叉验证

## 跑
```bash
make lab-L02
```

## 关键代码点
- `fixed_split` / `recursive_split` / `md_split` — 独立实现
- `coverage` — chunk 拼回原文档的比例
- 对比表：n / avg / max / min / coverage

## 经验法则
- **MD 文档** → MarkdownHeading（保 heading_path 利于过滤）
- **聊天/邮件** → Recursive（按段落 / 句号 fallback）
- **代码/固定 schema** → 按行/函数切，**不要用 Fixed**
- **chunk_size** 经验：中文 200–400 字，英文 200–500 token
- **overlap** 经验：10–20% chunk_size
