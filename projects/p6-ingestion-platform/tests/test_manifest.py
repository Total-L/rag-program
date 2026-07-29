"""P6 增量摄取单测 —— 这是 ROADMAP 里"incremental indexing"从声明变成实现的证据。

覆盖意图：
- 幂等（重跑 0 次重嵌）＝ 省钱与可扩展性的核心指标
- 删除识别正确，且**增量模式下绝不瞎猜删除**（防误删真实数据）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.manifest import ManifestStore  # noqa: E402
from src.models import SourceRecord  # noqa: E402


def rec(sid: str, etag: str = "v1", connector: str = "fs") -> SourceRecord:
    return SourceRecord(
        source_id=f"{connector}:{sid}",
        connector=connector,
        uri=f"file:///{sid}",
        source_format="md",
        etag=etag,
    )


@pytest.fixture()
def store(tmp_path: Path) -> ManifestStore:
    m = ManifestStore(tmp_path / "manifest.db")
    yield m
    m.close()


def test_first_run_everything_is_new(store: ManifestStore):
    d = store.diff([rec("a"), rec("b"), rec("c")], run_id="r1")
    assert d.summary() == {"new": 3, "changed": 0, "unchanged": 0, "deleted": 0}
    assert len(d.to_fetch) == 3, "首轮全部需要下载正文"


def test_rerun_is_idempotent_zero_reembed(store: ManifestStore):
    """★ 核心指标：同一批数据重跑，需要重嵌的文档数必须为 0。"""
    recs = [rec("a"), rec("b"), rec("c")]
    d1 = store.diff(recs, run_id="r1")
    for r in d1.to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=2)

    d2 = store.diff(recs, run_id="r2")
    assert d2.summary() == {"new": 0, "changed": 0, "unchanged": 3, "deleted": 0}
    assert d2.to_fetch == [], "幂等：重跑不该下载/重嵌任何文档"


def test_changed_etag_is_detected(store: ManifestStore):
    recs = [rec("a", "v1"), rec("b", "v1")]
    for r in store.diff(recs, run_id="r1").to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=1)

    d = store.diff([rec("a", "v1"), rec("b", "v2")], run_id="r2")
    assert d.summary()["changed"] == 1
    assert d.summary()["unchanged"] == 1
    assert d.to_fetch[0].source_id == "fs:b"


def test_full_scan_detects_deletion(store: ManifestStore):
    recs = [rec("a"), rec("b"), rec("c")]
    for r in store.diff(recs, run_id="r1").to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=1)

    d = store.diff(recs[:2], run_id="r2", full_scan=True)
    assert d.deleted == ["fs:c"], f"应识别出被删的文档, got {d.deleted}"


def test_incremental_mode_never_guesses_deletion(store: ManifestStore):
    """★ 防误删：增量模式下"没扫到"≠"被删除"（可能只是限流/没权限）。"""
    recs = [rec("a"), rec("b"), rec("c")]
    for r in store.diff(recs, run_id="r1").to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=1)

    d = store.diff(recs[:1], run_id="r2", full_scan=False)
    assert d.deleted == [], "增量模式绝不能猜删除，否则会误删真实数据"


def test_empty_change_key_treated_as_changed(store: ManifestStore):
    """既没 etag 又没正文 → 判不了，保守当作 changed（宁可多做不可漏做）。"""
    r = SourceRecord(source_id="fs:x", connector="fs", uri="u", source_format="md")
    assert r.change_key == ""
    store.diff([r], run_id="r1")
    store.mark_indexed(r, run_id="r1", chunk_count=1)
    d = store.diff([r], run_id="r2")
    assert d.summary()["changed"] == 1, "无法判定时应保守重做"


def test_unchanged_docs_still_get_touched(store: ManifestStore):
    """unchanged 也要打 last_seen_run，否则下一轮会被误判为已删除。"""
    recs = [rec("a"), rec("b")]
    for r in store.diff(recs, run_id="r1").to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=1)
    store.diff(recs, run_id="r2", full_scan=True)  # 全 unchanged
    d3 = store.diff(recs, run_id="r3", full_scan=True)
    assert d3.deleted == [], "unchanged 文档不该在后续轮被误判删除"


def test_forget_removes_from_manifest(store: ManifestStore):
    r = rec("a")
    store.diff([r], run_id="r1")
    store.mark_indexed(r, run_id="r1", chunk_count=3)
    assert store.stats() == {"docs": 1, "chunks": 3}
    store.forget(r.source_id)
    assert store.stats()["docs"] == 0
    assert store.diff([r], run_id="r2").summary()["new"] == 1, "忘掉后应重新视为 new"


def test_connector_scoped_diff_isolates_sources(store: ManifestStore):
    """一个连接器的全量扫描，不该把别的连接器的文档判成删除。"""
    fs_recs = [rec("a", connector="fs")]
    sql_recs = [rec("1", connector="sql")]
    for r in store.diff(fs_recs + sql_recs, run_id="r1").to_fetch:
        store.mark_indexed(r, run_id="r1", chunk_count=1)

    d = store.diff(fs_recs, run_id="r2", full_scan=True, connector="fs")
    assert d.deleted == [], "限定 connector=fs 时不该看到 sql 的文档"


def test_cursor_roundtrip_supports_resume(store: ManifestStore):
    assert store.get_cursor("fs") == ""
    store.set_cursor("fs", "2026-07-27T10:00:00")
    assert store.get_cursor("fs") == "2026-07-27T10:00:00"
    store.set_cursor("fs", "2026-07-28T10:00:00")
    assert store.get_cursor("fs") == "2026-07-28T10:00:00", "游标应可更新（断点恢复）"


def test_stats_counts_docs_and_chunks(store: ManifestStore):
    for i, r in enumerate([rec("a"), rec("b")]):
        store.diff([r], run_id="r1")
        store.mark_indexed(r, run_id="r1", chunk_count=i + 1)
    assert store.stats() == {"docs": 2, "chunks": 3}
