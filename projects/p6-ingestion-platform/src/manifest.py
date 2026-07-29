"""P6 — 增量摄取的状态存储（Manifest + Cursor）。

这是审计里"L14/ROADMAP 宣称 incremental indexing 但没实现"的真正补齐。

设计原则：
- **幂等**：同一批数据重跑，`diff()` 全判 UNCHANGED，下游 0 次重嵌、0 次写库。
- **能识别删除**：源侧删掉的文档必须从向量库里删掉，否则永远召回幽灵内容。
  用 `run_id` 打标：全量扫描时本轮没见到的 source_id 即为已删除。
  （增量/游标模式无法判断删除 —— 这点必须显式承认，见 `diff(full_scan=)`。）
- **游标可恢复**：中断后从 `cursors` 表接着跑，不从头再来。
- 只用 stdlib `sqlite3`：manifest 本身是运维状态，不该引入重依赖。

为什么不用向量库自己存状态：向量库里存的是"当前索引了什么"，
manifest 存的是"源侧看到了什么 + 什么时候看到的"。两者必须能对账，
所以要分开 —— 对账不上就是 bug，这正是我们要能查出来的东西。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from .models import ChangeType, SourceRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (
    source_id     TEXT PRIMARY KEY,
    connector     TEXT NOT NULL,
    uri           TEXT NOT NULL DEFAULT '',
    change_key    TEXT NOT NULL DEFAULT '',   -- etag 优先，退回 sha256
    sha256        TEXT NOT NULL DEFAULT '',
    mtime         REAL NOT NULL DEFAULT 0,
    last_seen_run TEXT NOT NULL DEFAULT '',   -- 用于识别删除
    indexed_at    REAL NOT NULL DEFAULT 0,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    tenant_id     TEXT NOT NULL DEFAULT ''    -- S2 多租户：按 tenant 分桶
);
CREATE INDEX IF NOT EXISTS idx_manifest_connector ON manifest(connector);
CREATE INDEX IF NOT EXISTS idx_manifest_tenant ON manifest(tenant_id);

CREATE TABLE IF NOT EXISTS cursors (
    connector  TEXT PRIMARY KEY,
    cursor     TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);
"""


@dataclass
class Delta:
    """一轮扫描的增量判定结果。"""

    new: list[SourceRecord] = field(default_factory=list)
    changed: list[SourceRecord] = field(default_factory=list)
    unchanged: list[SourceRecord] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)  # source_id 列表

    @property
    def to_fetch(self) -> list[SourceRecord]:
        """只有 new + changed 需要下载正文并重新嵌入 —— 这就是省钱的地方。"""
        return self.new + self.changed

    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
            "deleted": len(self.deleted),
        }


class ManifestStore:
    """SQLite 支撑的摄取状态存储。可当 context manager 用。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        with closing(self.conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        self.conn.commit()

    # ── 增量判定 ────────────────────────────────────
    def diff(
        self,
        records: list[SourceRecord],
        run_id: str,
        *,
        full_scan: bool = True,
        connector: str | None = None,
    ) -> Delta:
        """把"源侧当前看到的记录"和"manifest 里记的"比一比。

        Args:
            records: 本轮从连接器 list 出来的记录（可以只有元数据，无正文）
            run_id: 本轮运行 id，用于给见到的记录打标
            full_scan: True = 本轮是全量扫描，未见到的即判定为已删除。
                       False = 增量/游标模式，**不做删除判定**（判不了，别瞎猜）
            connector: 限定只在某个 connector 范围内判删除

        为什么 full_scan 要显式传：增量模式下"没见到"只是"这次没扫到"，
        当成删除会误删真实数据。这种模糊地带必须由调用方明确表态。
        """
        delta = Delta()
        known = self._load_known(connector)
        seen: set[str] = set()

        for rec in records:
            seen.add(rec.source_id)
            row = known.get(rec.source_id)
            if row is None:
                delta.new.append(rec)
            elif row["change_key"] != rec.change_key or not rec.change_key:
                # change_key 为空（源侧既没 etag 也没拉正文）→ 保守当作 changed
                delta.changed.append(rec)
            else:
                delta.unchanged.append(rec)

        if full_scan:
            delta.deleted = [sid for sid in known if sid not in seen]

        # 给本轮见到的都打上 last_seen_run（含 unchanged，否则下轮会被误判删除）
        self._touch_seen(seen, run_id)
        return delta

    def _load_known(self, connector: str | None) -> dict[str, sqlite3.Row]:
        sql = "SELECT * FROM manifest"
        params: tuple = ()
        if connector:
            sql += " WHERE connector = ?"
            params = (connector,)
        with closing(self.conn.cursor()) as cur:
            cur.execute(sql, params)
            return {r["source_id"]: r for r in cur.fetchall()}

    def _touch_seen(self, source_ids: set[str], run_id: str) -> None:
        if not source_ids:
            return
        with closing(self.conn.cursor()) as cur:
            cur.executemany(
                "UPDATE manifest SET last_seen_run = ? WHERE source_id = ?",
                [(run_id, sid) for sid in source_ids],
            )
        self.conn.commit()

    # ── 写入 ────────────────────────────────────────
    def mark_indexed(
        self,
        rec: SourceRecord,
        *,
        run_id: str,
        chunk_count: int,
        tenant_id: str = "_unassigned",
    ) -> None:
        """一篇文档成功写进向量库后调用。失败的**不要**调 → 下轮会重试。

        S2 多租户：必须传 tenant_id（写入 manifest 用于审计 + 隔离验证）。
        默认 `_unassigned` 是为了兼容 S2 之前的旧测试 / 旧数据迁移 —— 真正
        生产 ingest 路径已在 `run_ingest.py` CLI 强制要求 `--tenant`（拒绝
        空值透传到 mark_indexed），不会出现真正的无主数据。
        """
        # `if not tenant_id` 会把默认占位 `_unassigned` 也判空 —— 必须显式检查
        if tenant_id == "":
            raise ValueError(
                "mark_indexed requires tenant_id (S2 multi-tenancy: no orphan data allowed); "
                "explicitly pass '_unassigned' if migrating legacy data"
            )
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO manifest
                    (source_id, connector, uri, change_key, sha256, mtime,
                     last_seen_run, indexed_at, chunk_count, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    connector     = excluded.connector,
                    uri           = excluded.uri,
                    change_key    = excluded.change_key,
                    sha256        = excluded.sha256,
                    mtime         = excluded.mtime,
                    last_seen_run = excluded.last_seen_run,
                    indexed_at    = excluded.indexed_at,
                    chunk_count   = excluded.chunk_count,
                    tenant_id     = excluded.tenant_id
                """,
                (
                    rec.source_id,
                    rec.connector,
                    rec.uri,
                    rec.change_key,
                    rec.sha256,
                    rec.mtime,
                    run_id,
                    time.time(),
                    chunk_count,
                    tenant_id,
                ),
            )
        self.conn.commit()

    def forget(self, source_id: str) -> None:
        """源侧删除后，把 manifest 里的记录也删掉（向量库的删除由 IndexStore 负责）。"""
        with closing(self.conn.cursor()) as cur:
            cur.execute("DELETE FROM manifest WHERE source_id = ?", (source_id,))
        self.conn.commit()

    # ── 游标（可恢复）───────────────────────────────
    def get_cursor(self, connector: str) -> str:
        with closing(self.conn.cursor()) as cur:
            cur.execute("SELECT cursor FROM cursors WHERE connector = ?", (connector,))
            row = cur.fetchone()
            return row["cursor"] if row else ""

    def set_cursor(self, connector: str, cursor: str) -> None:
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO cursors (connector, cursor, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(connector) DO UPDATE SET
                    cursor = excluded.cursor, updated_at = excluded.updated_at
                """,
                (connector, cursor, time.time()),
            )
        self.conn.commit()

    # ── 观测 ────────────────────────────────────────
    def stats(self) -> dict[str, int]:
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                "SELECT COUNT(*) AS docs, COALESCE(SUM(chunk_count),0) AS chunks FROM manifest"
            )
            r = cur.fetchone()
            return {"docs": r["docs"], "chunks": r["chunks"]}

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ManifestStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["ManifestStore", "Delta", "ChangeType"]
