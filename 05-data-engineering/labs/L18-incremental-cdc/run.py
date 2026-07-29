"""L18 — 增量 / CDC：ManifestStore + delta。

教学目标：
1. 看到"真增量"的关键：`change_key = etag or sha256`
2. 看到 Delta 四象限：new / changed / unchanged / deleted
3. 理解"按源能力分档"：CDC > watermark > 全表 + 内容哈希

生产实现：`projects/p6-ingestion-platform/src/manifest.py`（SQLite + 完整实现）

运行：
    python 05-data-engineering/labs/L18-incremental-cdc/run.py

输出：
- artifacts/L18/manifest.db
- artifacts/L18/report.md
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ART = Path("artifacts/L18")
ART.mkdir(parents=True, exist_ok=True)
DB = ART / "manifest.db"
if DB.exists():
    DB.unlink()


# ── 1. 极简 ManifestStore（生产版见 P6 src/manifest.py） ─────────
class MiniManifest:
    """键值表：source_id → (change_key, indexed_at, run_id)。

    比生产版少了：
    - cursor 游标（连接器状态推进）
    - 失败重试队列
    - full_scan 模式（允许删判定）
    """

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                source_id TEXT PRIMARY KEY,
                change_key TEXT NOT NULL,
                indexed_at REAL NOT NULL,
                run_id TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0
            )""")
        self.conn.commit()

    def diff(self, listed: Iterable[dict], *, run_id: str, full_scan: bool = False):
        listed_ids = {r["source_id"]: r["change_key"] for r in listed}
        known = dict(self.conn.execute(
            "SELECT source_id, change_key FROM entries"
        ).fetchall())

        new = [r for r in listed if r["source_id"] not in known]
        changed = [r for r in listed
                   if r["source_id"] in known and known[r["source_id"]] != r["change_key"]]
        unchanged = [r for r in listed
                     if r["source_id"] in known and known[r["source_id"]] == r["change_key"]]
        if full_scan:
            deleted = [sid for sid in known if sid not in listed_ids]
        else:
            deleted = []    # ★ 增量模式"没扫到"≠"被删除"
        return {"new": new, "changed": changed, "unchanged": unchanged, "deleted": deleted}

    def mark_indexed(self, rec: dict, *, run_id: str, chunk_count: int = 0):
        self.conn.execute("""
            INSERT OR REPLACE INTO entries
            (source_id, change_key, indexed_at, run_id, chunk_count)
            VALUES (?, ?, ?, ?, ?)
        """, (rec["source_id"], rec["change_key"], 0, run_id, chunk_count))
        self.conn.commit()

    def stats(self) -> dict:
        return dict(self.conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN indexed_at > 0 THEN 1 ELSE 0 END) AS indexed
            FROM entries
        """).fetchone())


# ── 2. 演示 ──────────────────────────────────────────────
def run(name: str, listed: list[dict], *, full_scan: bool = False) -> dict:
    m = MiniManifest(DB)
    delta = m.diff(listed, run_id=name, full_scan=full_scan)
    print(f"\n─── {name} (full_scan={full_scan}) ───")
    print(f"  listed={len(listed)}  new={len(delta['new'])}  "
          f"changed={len(delta['changed'])}  unchanged={len(delta['unchanged'])}  "
          f"deleted={len(delta['deleted'])}")
    return delta


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L18 — 增量 / CDC（ManifestStore）")
    print("════════════════════════════════════════════\n")

    # 第一轮：3 篇文档全新入库
    docs_v1 = [
        {"source_id": "doc-A", "change_key": "etag-1"},
        {"source_id": "doc-B", "change_key": "etag-1"},
        {"source_id": "doc-C", "change_key": "etag-1"},
    ]
    delta = run("run-1", docs_v1)
    m = MiniManifest(DB)
    for r in docs_v1:
        m.mark_indexed(r, run_id="run-1")

    # 第二轮：A 改了、B 没变、C 删了、新增 D
    docs_v2 = [
        {"source_id": "doc-A", "change_key": "etag-2"},   # changed
        {"source_id": "doc-B", "change_key": "etag-1"},   # unchanged
        # doc-C missing → candidate for deleted
        {"source_id": "doc-D", "change_key": "etag-1"},   # new
    ]
    delta = run("run-2 (增量模式)", docs_v2, full_scan=False)
    # 增量模式：deleted=[]（不判删）
    assert delta["deleted"] == [], "增量模式不应判删"

    delta = run("run-2 (全量模式)", docs_v2, full_scan=True)
    # 全量模式：deleted=[doc-C]
    assert delta["deleted"] == ["doc-C"], "全量模式应识别 doc-C 已删"
    print(f"  ✓ 全量模式下识别到删除: {delta['deleted']}")

    (ART / "report.md").write_text(
        "# L18 — 增量/CDC 报告\n\n"
        "## 关键观察\n"
        "- **change_key = etag or sha256**：优先用源侧版本号（不需要下载正文）\n"
        "- **三档增量能力**：CDC（最实时）> watermark 列（覆盖 95%）> 全表 + 内容哈希（兜底）\n"
        "- **删除判定分场景**：\n"
        "  - `full_scan=True`：本轮没见到的 = 已删除\n"
        "  - `full_scan=False`：不判删（增量模式\"没扫到\"≠\"被删除\"）\n"
        "- **SaaS 连接器不能判删**：API 限流/权限差异会让列举不完整\n\n"
        "## 生产版\n"
        "`projects/p6-ingestion-platform/src/manifest.py` 多了：\n"
        "- cursor 推进（连接器状态）\n"
        "- 失败重试（失败不 mark_indexed，下轮自动重试）\n"
        "- 完整 SQLite schema（多连接器隔离）\n\n"
        "## 面试题\n"
        "1. 为什么 watermark 列用 `>=` 而非 `>`？\n"
        "2. CDC（Debezium）和 watermark 列的取舍？\n"
        "3. SaaS 连接器 `supports_full_scan()` 返回 False 的设计意义？\n",
        encoding="utf-8",
    )
    print(f"\n✓ 报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()