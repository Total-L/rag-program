# L17 — 连接器报告

## 列举统计
- 共 **153** 条记录来自 3 个数据源

## 关键观察
- **两阶段协议**：list_records 只取元数据（cheap），fetch_body 只对 new/changed（expensive）
- **统一 SourceRecord**：下游（清洗 / 嵌入 / 向量库）只认这一个类型
- **provenance 必须在摄取那一刻捕获**（author/etag/acl_principals），事后补不回来
- **增量靠 etag/版本号**：传 since_cursor 给连接器 → 只返回 etag 更大的

## 生产版
看 `projects/p6-ingestion-platform/src/connectors/`：
- `fs.py` — 真实可跑（mtime_ns + size 当 etag）
- `sql.py` — 真实可跑（SQLAlchemy + watermark 列）
- `objectstore.py` — 本地替身 + boto3 S3 桩
- `confluence.py` — fixture 回放 + 真实 API 切换

## 面试题
1. 为什么 list_records 必须在 fetch_body 之前？合成一步的后果是什么？
2. SaaS 连接器（Confluence / Salesforce）支持 `supports_full_scan()` 返回 False 的设计意义？
3. watermark 列用 `>=` 而非 `>` 的取舍？
