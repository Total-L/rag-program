# L18 — 增量 / CDC

> "怎么提取"的第二步：怎么只**对真正变化的文档**干活。

## 学习目标
- 看到 `change_key = etag or sha256` 的设计——优先用源侧版本号，省下载
- 看到 Delta 四象限：new / changed / unchanged / deleted
- 理解"按源能力分档"：CDC > watermark > 全表 + 内容哈希

## 跑

```bash
make lab-L18
```

## 关键代码点
- `MiniManifest.diff()` —— 把 listing 和库内比对，返回 4 类
- **删除判定分场景**：`full_scan=True` 才允许判删
- `mark_indexed()` —— 只在成功后才标，下次才能判 unchanged

## 玩具版 vs 生产版

| | L18 | P6 |
|---|---|---|
| 存储 | SQLite 单表 | SQLite 多连接器隔离 |
| 游标 | 无 | per-connector cursor |
| 失败重试 | 无 | 失败不 mark → 下轮自动重试 |
| 删除模式 | full_scan 开关 | 同 + SaaS supports_full_scan 检查 |

## 输出
- `artifacts/L18/manifest.db` —— 真实 SQLite，可 SQL 探查
- `artifacts/L18/report.md`

## 面试题
1. 为什么 watermark 列用 `>=` 而非 `>`？
2. CDC（Debezium）和 watermark 列的取舍？
3. SaaS 连接器 `supports_full_scan()` 返回 False 的设计意义？