"""P6 连接器单测 —— "怎么提取企业级数据"的回归测试。

覆盖意图：
- **两阶段协议**：list 阶段绝不能带正文（否则增量等于全量下载）
- provenance 在摄取时就捕获（事后补不回来）
- SaaS 源不允许据列举判删除（防误删）
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.connectors import (  # noqa: E402
    ConfluenceConnector,
    FileSystemConnector,
    ObjectStoreConnector,
    SQLConnector,
    frontmatter_provenance,
)

REPO = HERE.parent.parent.parent  # rag_program/
ENTERPRISE_FIXTURES = REPO / "datasets" / "fixtures" / "enterprise"
CONFLUENCE_FIXTURE = HERE.parent / "fixtures" / "confluence_pages.json"


# ── frontmatter 解析（原来散在 P1 里的手搓逻辑）──────────
def test_frontmatter_provenance_extracts_and_strips():
    text = "---\nsensitivity: restricted\nowner: alice@acme.com\n---\n正文内容"
    meta, body = frontmatter_provenance(text)
    assert meta["sensitivity"] == "restricted"
    assert meta["owner"] == "alice@acme.com"
    assert body.strip() == "正文内容", "frontmatter 应从正文中剥离"


def test_frontmatter_absent_is_safe():
    meta, body = frontmatter_provenance("没有 frontmatter 的正文")
    assert meta == {}
    assert body == "没有 frontmatter 的正文"


def test_frontmatter_unterminated_is_safe():
    meta, body = frontmatter_provenance("---\nsensitivity: x\n没有结束标记")
    assert meta == {}, "残缺 frontmatter 不该乱解析"


# ── ① 文件系统连接器 ─────────────────────────────────
@pytest.mark.skipif(not ENTERPRISE_FIXTURES.exists(), reason="企业 fixture 不存在")
def test_fs_connector_list_carries_no_body():
    """★ 两阶段协议：list 阶段必须 raw is None。"""
    conn = FileSystemConnector(ENTERPRISE_FIXTURES, patterns=("*.md",))
    recs = list(conn.list_records())
    assert recs, "应列举到文档"
    assert all(r.raw is None for r in recs), "list 阶段不得下载正文"
    assert all(r.etag for r in recs), "必须有 etag 才能做增量"


@pytest.mark.skipif(not ENTERPRISE_FIXTURES.exists(), reason="企业 fixture 不存在")
def test_fs_connector_captures_provenance_from_frontmatter():
    conn = FileSystemConnector(ENTERPRISE_FIXTURES, patterns=("*.md",))
    recs = list(conn.list_records())
    with_owner = [r for r in recs if r.author]
    assert with_owner, "企业 fixture 带 owner，应被抽成 author"
    assert {r.sensitivity for r in recs} <= {"public", "internal", "restricted"}
    assert any(r.sensitivity == "restricted" for r in recs), "应识别出 restricted 文档"


@pytest.mark.skipif(not ENTERPRISE_FIXTURES.exists(), reason="企业 fixture 不存在")
def test_fs_connector_fetch_body_returns_bytes():
    conn = FileSystemConnector(ENTERPRISE_FIXTURES, patterns=("*.md",))
    rec = next(iter(conn.list_records()))
    body = conn.fetch_body(rec)
    assert isinstance(body, bytes) and body


def test_fs_connector_etag_changes_on_edit(tmp_path: Path):
    p = tmp_path / "a.md"
    p.write_text("原始内容", encoding="utf-8")
    conn = FileSystemConnector(tmp_path)
    e1 = next(iter(conn.list_records())).etag
    p.write_text("修改后的内容变长了", encoding="utf-8")
    e2 = next(iter(FileSystemConnector(tmp_path).list_records())).etag
    assert e1 != e2, "内容改了 etag 必须变（否则增量检测失效）"


def test_fs_connector_since_cursor_skips_old(tmp_path: Path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    conn = FileSystemConnector(tmp_path)
    all_recs = list(conn.list_records())
    assert len(all_recs) == 1
    cursor = conn.next_cursor()
    assert list(FileSystemConnector(tmp_path).list_records(since_cursor=cursor)) == [], (
        "游标之后无变化时应返回空"
    )


# ── ② SQL 连接器 ─────────────────────────────────────
@pytest.fixture()
def biz_db(tmp_path: Path) -> str:
    db = tmp_path / "biz.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE tickets(id INTEGER PRIMARY KEY, title TEXT, body TEXT,"
        " updated_at TEXT, owner TEXT, sens TEXT)"
    )
    con.executemany(
        "INSERT INTO tickets VALUES(?,?,?,?,?,?)",
        [
            (1, "登录失败", "SSO 报 500", "2026-07-01T10:00:00", "王强", "internal"),
            (2, "发票开具", "需要专票", "2026-07-02T11:00:00", "李静", "restricted"),
            (3, "功能建议", "支持导出", "2026-07-03T09:30:00", "张敏", "public"),
        ],
    )
    con.commit()
    con.close()
    return f"sqlite:///{db}"


def test_sql_connector_generates_correct_sql(biz_db: str):
    c = SQLConnector(biz_db, "tickets", text_cols=("title", "body"), updated_col="updated_at")
    full = c.select_sql(incremental=False)
    inc = c.select_sql(incremental=True)
    assert "WHERE" not in full
    assert "WHERE updated_at >= :cursor" in inc, "watermark 用 >= 避免漏边界行"
    assert "ORDER BY updated_at, id" in inc, "必须稳定排序，否则游标不可靠"


def test_sql_connector_extracts_rows_with_provenance(biz_db: str):
    c = SQLConnector(
        biz_db,
        "tickets",
        text_cols=("title", "body"),
        updated_col="updated_at",
        author_col="owner",
        sensitivity_col="sens",
    )
    recs = list(c.list_records())
    assert len(recs) == 3
    assert recs[1].author == "李静"
    assert recs[1].sensitivity == "restricted"
    assert recs[0].etag == "2026-07-01T10:00:00", "etag 用 watermark 列"
    body = c.fetch_body(recs[1]).decode()
    assert "发票开具" in body and "需要专票" in body, "多列应拼成正文"


def test_sql_connector_incremental_includes_boundary(biz_db: str):
    """`>=` 语义：宁可多读边界那行，也不能漏。"""
    c = SQLConnector(biz_db, "tickets", updated_col="updated_at")
    inc = list(c.list_records(since_cursor="2026-07-02T11:00:00"))
    assert len(inc) == 2, f"应含边界行, got {[r.extra['row_id'] for r in inc]}"


def test_sql_connector_cursor_is_max_watermark(biz_db: str):
    c = SQLConnector(biz_db, "tickets", updated_col="updated_at")
    list(c.list_records())
    assert c.next_cursor() == "2026-07-03T09:30:00"


# ── ③ 对象存储连接器 ─────────────────────────────────
@pytest.fixture()
def bucket(tmp_path: Path) -> Path:
    root = tmp_path / "bucket"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "contract.txt").write_text("合同正文", encoding="utf-8")
    (root / "docs" / "report.md").write_text("# 月报", encoding="utf-8")
    return root


def test_objectstore_local_lists_with_etag(bucket: Path):
    c = ObjectStoreConnector("kb", backend="local", local_root=bucket)
    recs = list(c.list_records())
    assert len(recs) == 2
    assert all(len(r.etag) == 32 for r in recs), "本地后端 etag 用 md5（同 S3 语义）"
    assert all(r.uri.startswith("s3://kb/") for r in recs)
    assert all(r.raw is None for r in recs), "list 阶段不得带正文"


def test_objectstore_fetch_body(bucket: Path):
    c = ObjectStoreConnector("kb", backend="local", local_root=bucket)
    rec = [r for r in c.list_records() if r.source_format == "txt"][0]
    assert c.fetch_body(rec).decode() == "合同正文"


def test_objectstore_etag_changes_on_edit(bucket: Path):
    c1 = ObjectStoreConnector("kb", backend="local", local_root=bucket)
    before = {r.source_id: r.etag for r in c1.list_records()}
    (bucket / "docs" / "report.md").write_text("# 月报 v2", encoding="utf-8")
    c2 = ObjectStoreConnector("kb", backend="local", local_root=bucket)
    after = {r.source_id: r.etag for r in c2.list_records()}
    changed = [k for k in before if before[k] != after[k]]
    assert len(changed) == 1 and "report.md" in changed[0]


def test_objectstore_rejects_bad_config():
    with pytest.raises(ValueError):
        ObjectStoreConnector("kb", backend="local")  # 缺 local_root
    with pytest.raises(ValueError):
        ObjectStoreConnector("kb", backend="gcs")  # 未知后端


# ── ④ Confluence 连接器 ──────────────────────────────
@pytest.mark.skipif(not CONFLUENCE_FIXTURE.exists(), reason="confluence fixture 缺失")
def test_confluence_uses_native_version_as_etag():
    c = ConfluenceConnector(fixture_path=CONFLUENCE_FIXTURE)
    recs = list(c.list_records())
    assert len(recs) == 3
    assert recs[0].etag == "v3", "原生版本号是最便宜的 change_key"
    assert recs[0].author == "李静"
    assert recs[0].source_format == "html", "storage format 是 XHTML，复用 html loader"


@pytest.mark.skipif(not CONFLUENCE_FIXTURE.exists(), reason="confluence fixture 缺失")
def test_confluence_maps_labels_to_sensitivity():
    c = ConfluenceConnector(fixture_path=CONFLUENCE_FIXTURE)
    by_id = {r.source_id: r for r in c.list_records()}
    assert by_id["confluence:1002"].sensitivity == "restricted"
    assert by_id["confluence:1003"].sensitivity == "public"
    assert by_id["confluence:1001"].sensitivity == "internal", "无显式标签默认 internal"
    assert "acl-sre" in by_id["confluence:1002"].acl_principals


@pytest.mark.skipif(not CONFLUENCE_FIXTURE.exists(), reason="confluence fixture 缺失")
def test_confluence_forbids_full_scan_deletion():
    """★ 防误删：SaaS 列举不可靠，不能据此判定"文档被删除"。"""
    c = ConfluenceConnector(fixture_path=CONFLUENCE_FIXTURE)
    assert c.supports_full_scan() is False


def test_confluence_requires_url_or_fixture():
    with pytest.raises(ValueError):
        ConfluenceConnector()
