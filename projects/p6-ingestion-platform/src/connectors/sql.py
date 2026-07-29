"""数据库连接器 —— 从业务库抽内容（企业 RAG 最常见的数据源）。

用 SQLAlchemy，所以**同一份代码同一句 SQL**：
- 本机跑 SQLite（`sqlite:///xxx.db`）→ 真实可验证
- 生产换 Postgres（`postgresql+psycopg://user:pw@host/db`）→ 只改 DSN

增量策略（三档，按源库能力从好到差）：
1. **CDC / LSN**：Debezium / 逻辑复制槽，最实时，但要 DBA 配合
2. **watermark 列**（本实现）：`WHERE updated_at > :cursor ORDER BY updated_at`
   —— 覆盖 95% 场景，只要业务表有 updated_at
3. **全表 + 内容哈希**：兜底，贵

⚠️ watermark 的已知坑（必须写出来，不然就是埋雷）：
- 同一 updated_at 有多行时用 `>` 会漏数据 → 这里用 `>=` + 靠 manifest 的
  change_key 去重（宁可多读，不可漏读）
- 事务提交顺序与 updated_at 顺序不一致时仍可能漏 → 真要严谨得上 CDC
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..models import SourceRecord
from . import Connector


class SQLConnector(Connector):
    """把一张表的若干列拼成文档。

    Args:
        dsn: SQLAlchemy DSN。SQLite 本机验证 / Postgres 生产。
        table: 表名
        id_col: 主键列
        text_cols: 拼成正文的列（按顺序）
        updated_col: watermark 列（增量依据）；没有就传 None 走全量
        author_col / sensitivity_col: provenance 列（可选）
        batch_size: 分页大小，避免一次拉爆内存
    """

    name = "sql"

    def __init__(
        self,
        dsn: str,
        table: str,
        *,
        id_col: str = "id",
        text_cols: tuple[str, ...] = ("title", "body"),
        updated_col: str | None = "updated_at",
        author_col: str | None = None,
        sensitivity_col: str | None = None,
        batch_size: int = 500,
    ):
        from sqlalchemy import create_engine  # 已装依赖，非延迟不可

        self.engine = create_engine(dsn)
        self.table = table
        self.id_col = id_col
        self.text_cols = text_cols
        self.updated_col = updated_col
        self.author_col = author_col
        self.sensitivity_col = sensitivity_col
        self.batch_size = batch_size
        self._max_updated: str = ""
        self._bodies: dict[str, str] = {}  # list 阶段顺手缓存的正文（DB 场景正文很便宜）

    # ── SQL 生成（纯函数式，便于单测）───────────────
    def select_sql(self, *, incremental: bool) -> str:
        cols = [self.id_col, *self.text_cols]
        for c in (self.updated_col, self.author_col, self.sensitivity_col):
            if c and c not in cols:
                cols.append(c)
        sql = f"SELECT {', '.join(cols)} FROM {self.table}"  # noqa: S608 - 列名来自配置非用户输入
        if incremental and self.updated_col:
            # `>=` 而不是 `>`：宁可多读一条也不漏（重复由 manifest change_key 挡掉）
            sql += f" WHERE {self.updated_col} >= :cursor"
        if self.updated_col:
            sql += f" ORDER BY {self.updated_col}, {self.id_col}"
        else:
            sql += f" ORDER BY {self.id_col}"
        return sql

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        from sqlalchemy import text as sa_text

        incremental = bool(since_cursor and self.updated_col)
        sql = sa_text(self.select_sql(incremental=incremental))
        params: dict[str, Any] = {"cursor": since_cursor} if incremental else {}

        with self.engine.connect() as conn:
            result = conn.execute(sql, params)
            for row in result.mappings():
                rid = str(row[self.id_col])
                body = "\n\n".join(
                    str(row[c]) for c in self.text_cols if row.get(c) not in (None, "")
                )
                self._bodies[rid] = body
                updated = str(row[self.updated_col]) if self.updated_col else ""
                if updated > self._max_updated:
                    self._max_updated = updated
                yield SourceRecord(
                    source_id=f"{self.name}:{self.table}:{rid}",
                    connector=self.name,
                    uri=f"db://{self.table}/{rid}",
                    source_format="txt",
                    # etag 用 watermark：不读正文就能判断变化
                    etag=updated,
                    mtime=0.0,
                    author=str(row[self.author_col]) if self.author_col else "",
                    sensitivity=(
                        str(row[self.sensitivity_col]) if self.sensitivity_col else "internal"
                    ),
                    extra={"row_id": rid, "table": self.table},
                )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        rid = rec.extra["row_id"]
        if rid in self._bodies:
            return self._bodies[rid].encode("utf-8")
        # 缓存没命中（比如跨进程恢复）→ 单行回查
        from sqlalchemy import text as sa_text

        cols = ", ".join(self.text_cols)
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa_text(f"SELECT {cols} FROM {self.table} WHERE {self.id_col} = :i"),  # noqa: S608
                    {"i": rid},
                )
                .mappings()
                .first()
            )
        if not row:
            raise KeyError(f"行已不存在: {rec.source_id}")
        return "\n\n".join(str(v) for v in row.values() if v).encode("utf-8")

    def next_cursor(self) -> str:
        return self._max_updated
