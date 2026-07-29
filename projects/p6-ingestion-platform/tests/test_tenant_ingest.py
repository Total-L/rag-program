"""S2 多租户：P6 摄取强制 tenant_id 测试。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# 让 `from src.manifest import ...` 能找到 —— 与同目录 test_manifest.py 同一惯例
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.manifest import ManifestStore, SourceRecord  # noqa: E402
from src.models import Chunk  # noqa: E402


@pytest.fixture
def manifest_path() -> Path:
    """临时 SQLite manifest db。"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "manifest.db"


def _record(
    source_id: str = "doc-1", connector: str = "fs", uri: str = "/tmp/x.md"
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        connector=connector,
        uri=uri,
        source_format="md",
        etag="v1",
        mtime=1.0,
    )


def test_mark_indexed_requires_tenant_id(manifest_path: Path) -> None:
    """mark_indexed 不传 tenant_id 必须 raise —— 防止无主数据落库。"""
    store = ManifestStore(manifest_path)
    rec = _record()
    with pytest.raises(ValueError, match="tenant_id"):
        store.mark_indexed(rec, run_id="r1", chunk_count=3, tenant_id="")


def test_mark_indexed_persists_tenant_id(manifest_path: Path) -> None:
    """mark_indexed 写入后能用 SQL 查到 tenant_id。"""
    store = ManifestStore(manifest_path)
    rec = _record(source_id="acme-doc")
    store.mark_indexed(rec, run_id="r1", chunk_count=3, tenant_id="acme-corp")
    store.close()

    # 重新打开验证
    conn = sqlite3.connect(manifest_path)
    cur = conn.cursor()
    cur.execute("SELECT tenant_id, chunk_count FROM manifest WHERE source_id = ?", ("acme-doc",))
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "acme-corp"
    assert row[1] == 3
    conn.close()


def test_multiple_tenants_isolated(manifest_path: Path) -> None:
    """两个租户的 manifest 互相独立 —— 按 tenant_id 能精确查询。"""
    store = ManifestStore(manifest_path)
    store.mark_indexed(_record(source_id="a-doc"), run_id="r1", chunk_count=2, tenant_id="acme")
    store.mark_indexed(_record(source_id="b-doc"), run_id="r1", chunk_count=5, tenant_id="globex")
    store.close()

    conn = sqlite3.connect(manifest_path)
    cur = conn.cursor()
    cur.execute("SELECT source_id FROM manifest WHERE tenant_id = ?", ("acme",))
    rows_acme = cur.fetchall()
    cur.execute("SELECT source_id FROM manifest WHERE tenant_id = ?", ("globex",))
    rows_globex = cur.fetchall()
    conn.close()

    assert rows_acme == [("a-doc",)]
    assert rows_globex == [("b-doc",)]


def test_chunk_to_metadata_includes_tenant_id() -> None:
    """Chunk.to_metadata 落地 tenant_id —— vector store where 过滤的前提。"""
    c = Chunk(
        chunk_id="abc",
        source_id="doc-1",
        text="hello",
        ordinal=0,
        heading_path="",
    )
    prov = {"sensitivity": "public", "tenant_id": "acme-corp"}
    md = c.to_metadata(prov)
    assert md["tenant_id"] == "acme-corp"
    assert md["sensitivity"] == "public"


def test_run_ingest_cli_requires_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """--tenant 缺省必须 raise SystemExit(2) —— 拒绝无主摄取。"""
    from run_ingest import main

    monkeypatch.setattr("sys.argv", ["run_ingest.py"])
    with pytest.raises(SystemExit):
        main()


def test_run_ingest_cli_accepts_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--tenant 显式传参时不报错（只走到 manifest 创建，不跑 connect）。"""
    from run_ingest import main

    monkeypatch.setattr(
        "sys.argv",
        ["run_ingest.py", "--stats", "--tenant", "acme-corp", "--data-dir", str(tmp_path)],
    )
    # --stats 模式只打印状态后退出，不连 connector / embedder
    rc = main()
    assert rc == 0
