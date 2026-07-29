# L18 — 增量/CDC 报告

## 关键观察
- **change_key = etag or sha256**：优先用源侧版本号（不需要下载正文）
- **三档增量能力**：CDC（最实时）> watermark 列（覆盖 95%）> 全表 + 内容哈希（兜底）
- **删除判定分场景**：
  - `full_scan=True`：本轮没见到的 = 已删除
  - `full_scan=False`：不判删（增量模式"没扫到"≠"被删除"）
- **SaaS 连接器不能判删**：API 限流/权限差异会让列举不完整

## 生产版
`projects/p6-ingestion-platform/src/manifest.py` 多了：
- cursor 推进（连接器状态）
- 失败重试（失败不 mark_indexed，下轮自动重试）
- 完整 SQLite schema（多连接器隔离）

## 面试题
1. 为什么 watermark 列用 `>=` 而非 `>`？
2. CDC（Debezium）和 watermark 列的取舍？
3. SaaS 连接器 `supports_full_scan()` 返回 False 的设计意义？
