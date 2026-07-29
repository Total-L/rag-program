# L19 — 数据清洗 / 归一化 / 近重复

> "怎么优化数据"的第一步。

## 学习目标
- 理解**归一化必须先于 chunk_id 计算**的硬要求（全角/零宽不归一 → 永远在重嵌）
- 看到"语料级样板"自动发现 vs "正则样板"
- 看到 MinHash + LSH 近重复检测的 30 行手写版本
- 看到"**阈值必须按文本长度校准**"这个坑

## 跑

```bash
make lab-L19
```

## 关键代码点
- `normalize()` —— NFKC + 零宽 + CRLF + 空白折叠
- `find_repeated_lines()` —— 跨文档频率统计
- `minhash()` / `jaccard()` —— 手写 30 行 MinHash（不引 `datasketch`）
- **★ 阈值按长度校准**：长文档 0.92 / 短句 ~0.6

## 输出
- `artifacts/L19/report.md`

## 面试题
1. 为什么归一化要在 chunk_id 计算之前？
2. 阈值 0.92 对 30 字短句近重复为什么漏？
3. LSH banding 的取舍（b 越大越敏感但越慢）？