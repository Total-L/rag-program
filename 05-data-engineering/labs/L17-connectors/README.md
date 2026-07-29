# L17 — 企业数据源连接器 + provenance 信封

> "怎么提取企业级数据"的入门课。

## 学习目标
- 理解**两阶段协议**：`list_records()` 只取元数据（便宜）vs `fetch_body()` 只对 new/changed 取正文（贵）
- 看到统一的 `SourceRecord` 信封——下游完全不感知来源是 S3 还是 Confluence
- 理解 `provenance` 必须在摄取那一刻捕获，事后补不回来

## 跑

```bash
make lab-L17
# 或
python 05-data-engineering/labs/L17-connectors/run.py
```

## 关键代码点
- `SourceRecord` —— 7 个最小字段（`source_id / uri / author / mtime / etag / sensitivity / acl_principals`）
- `LocalFSConnector / FakeSQLConnector / FakeConfluenceConnector` —— 三个替身，同一接口
- **增量演示**：`since_cursor="2026-01-15"` → 只返回 etag 更大的

## 玩具版 vs 生产版

| | L17（教学） | P6 生产 |
|---|---|---|
| 文件系统 | 内存 glob | 真实路径（mtime_ns+size 当 etag） |
| SQL | 内存列表 | SQLAlchemy + watermark 列 |
| 对象存储 | —— | 本地列举 + boto3 S3 桩 |
| Confluence | 内存 fixture | fixture 回放 + 真实 REST API |

## 输出
- `artifacts/L17/source_records.json` —— 三源汇总的 SourceRecord 列表
- `artifacts/L17/report.md` —— 报告

## 面试题
1. 为什么 `list_records` 必须在 `fetch_body` 之前？合成一步的后果是什么？
2. SaaS 连接器（Confluence / Salesforce）支持 `supports_full_scan()` 返回 False 的设计意义？
3. watermark 列用 `>=` 而非 `>` 的取舍？