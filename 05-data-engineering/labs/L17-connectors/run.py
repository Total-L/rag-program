"""L17 — 企业数据源连接器 + provenance 信封。

教学目标：
1. 看到"两阶段协议"：list_records() 只取元数据，fetch_body() 只对 new/changed 取正文
2. 看到统一的 SourceRecord 信封（下游完全不感知来源）
3. 对照企业级 vs 玩具级的差距

生产实现：`projects/p6-ingestion-platform/src/connectors/`（fs / sql / objectstore / confluence）

运行：
    python 05-data-engineering/labs/L17-connectors/run.py

输出：
- artifacts/L17/source_records.json
- artifacts/L17/report.md
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

ART = Path("artifacts/L17")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. SourceRecord 信封（精简版；生产版见 P6 src/models.py） ──────
@dataclass
class SourceRecord:
    """统一来源信封。

    为什么必须有这层：
    - 下游（清洗 / 嵌入 / 向量库）不应该知道 chunk 来自 S3 还是 Confluence
    - provenance 必须在摄取那一刻捕获 —— 事后补不回来（合规审计）
    """
    source_id: str
    uri: str
    author: str
    mtime: str
    etag: str
    sensitivity: str = "internal"
    acl_principals: list[str] = field(default_factory=list)
    raw: bytes | None = None          # 只有 fetch_body 后才有
    source_format: str = "md"

    def with_body(self, raw: bytes) -> "SourceRecord":
        c = dataclass.replace(self, raw=raw)
        return c


# ── 2. 三种连接器，统一实现 list_records / fetch_body ─────────
class LocalFSConnector:
    """替身：本地目录当共享盘 / NAS。生产换挂载路径即可。"""

    def __init__(self, root: Path, patterns: tuple[str, ...] = ("*.md",)):
        self.root = root
        self.patterns = patterns
        self.name = "fs"

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        for p in sorted(self.root.glob("*.md") if "*.md" in self.patterns else []):
            stat = p.stat()
            etag = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
            if etag <= since_cursor:
                continue
            yield SourceRecord(
                source_id=p.stem,
                uri=str(p),
                author="unknown",
                mtime=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                etag=etag,
            )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        return Path(rec.uri).read_bytes()


class FakeSQLConnector:
    """替身：内存 SQL（生产 = Postgres / MySQL，只换 DSN）。"""
    name = "sql"

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        for r in self.rows:
            etag = f"{r['updated_at']}-{r['id']}"
            if etag <= since_cursor:
                continue
            yield SourceRecord(
                source_id=f"ticket-{r['id']}",
                uri=f"sql://tickets/{r['id']}",
                author=r.get("author", "unknown"),
                mtime=r["updated_at"],
                etag=etag,
                source_format="md",
            )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        rid = int(rec.source_id.split("-")[-1])
        row = next(r for r in self.rows if r["id"] == rid)
        return f"# {row['title']}\n\n{row['body']}".encode("utf-8")


class FakeConfluenceConnector:
    """替身：录制 JSON fixture 回放。生产 = 真实 Confluence REST API。"""
    name = "confluence"

    def __init__(self, pages: list[dict]):
        self.pages = pages

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        for p in self.pages:
            etag = f"v{p['version']}"
            if etag <= since_cursor:
                continue
            yield SourceRecord(
                source_id=f"cf-{p['id']}",
                uri=f"https://confluence.corp/x/{p['id']}",
                author=p.get("author", "unknown"),
                mtime=p.get("updated", ""),
                etag=etag,
                sensitivity=p.get("sensitivity", "internal"),
                source_format="html",
            )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        pid = rec.source_id.split("-")[1]
        page = next(p for p in self.pages if str(p["id"]) == pid)
        return page["html"].encode("utf-8")


# ── 3. 演示 ──────────────────────────────────────────────
def main() -> None:
    print("════════════════════════════════════════════")
    print("  L17 — 企业数据源连接器")
    print("════════════════════════════════════════════\n")

    # 准备替身数据
    fs_root = Path("datasets/fixtures/enterprise")
    if not fs_root.exists():
        fs_root.mkdir(parents=True, exist_ok=True)
        (fs_root / "sample.md").write_text("# 示例\n\n本地文档内容。\n", encoding="utf-8")

    sql_rows = [
        {"id": 1, "title": "VPN 配置", "body": "vpn.corp.example.com", "updated_at": "2026-01-15", "author": "alice"},
        {"id": 2, "title": "年假", "body": "5-15 天按司龄", "updated_at": "2026-01-16", "author": "bob"},
    ]
    cf_pages = [
        {"id": "100", "title": "差旅政策", "version": 3, "author": "carol",
         "updated": "2026-02-01", "sensitivity": "public",
         "html": "<h1>差旅</h1><p>住宿一线 800/晚</p>"},
    ]

    connectors = [
        LocalFSConnector(fs_root),
        FakeSQLConnector(sql_rows),
        FakeConfluenceConnector(cf_pages),
    ]

    records: list[dict] = []
    for c in connectors:
        print(f"─── {c.name} ───")
        listed = list(c.list_records())
        for rec in listed:
            body = c.fetch_body(rec)
            print(f"  {rec.source_id:30s} etag={rec.etag:25s} body={len(body):4d}B  sensitivity={rec.sensitivity}")
            records.append({
                **asdict(rec), "raw_size": len(body),
                "body_preview": body[:60].decode("utf-8", "replace"),
            })

    # 演示增量：传游标 → 只返回 etag 更大的
    print("\n─── 增量演示（since_cursor='2026-01-15'）───")
    cursor = "2026-01-15"
    for c in connectors:
        n = sum(1 for _ in c.list_records(since_cursor=cursor))
        print(f"  {c.name}: 增量后剩 {n} 条")

    (ART / "source_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (ART / "report.md").write_text(
        "# L17 — 连接器报告\n\n"
        f"## 列举统计\n- 共 **{len(records)}** 条记录来自 {len(connectors)} 个数据源\n\n"
        "## 关键观察\n"
        "- **两阶段协议**：list_records 只取元数据（cheap），fetch_body 只对 new/changed（expensive）\n"
        "- **统一 SourceRecord**：下游（清洗 / 嵌入 / 向量库）只认这一个类型\n"
        "- **provenance 必须在摄取那一刻捕获**（author/etag/acl_principals），事后补不回来\n"
        "- **增量靠 etag/版本号**：传 since_cursor 给连接器 → 只返回 etag 更大的\n\n"
        "## 生产版\n"
        "看 `projects/p6-ingestion-platform/src/connectors/`：\n"
        "- `fs.py` — 真实可跑（mtime_ns + size 当 etag）\n"
        "- `sql.py` — 真实可跑（SQLAlchemy + watermark 列）\n"
        "- `objectstore.py` — 本地替身 + boto3 S3 桩\n"
        "- `confluence.py` — fixture 回放 + 真实 API 切换\n\n"
        "## 面试题\n"
        "1. 为什么 list_records 必须在 fetch_body 之前？合成一步的后果是什么？\n"
        "2. SaaS 连接器（Confluence / Salesforce）支持 `supports_full_scan()` 返回 False 的设计意义？\n"
        "3. watermark 列用 `>=` 而非 `>` 的取舍？\n",
        encoding="utf-8",
    )
    print(f"\n✓ 报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()