"""P4 table + time + search + embed 单测（ENTERPRISE_AUDIT C2 扩写）。

覆盖 parse_table(多种 markdown 格式)/ table_to_text 序列化 /
fake_embed 一致性 / search(排序 + 时间过滤 before / k 边界)/
端到端 main() 在 fixtures 上的运行。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

# 强制只插入 P4 src,避免 P2/P3 干扰
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path = [p for p in sys.path if "p2-code-docs" not in p and "p3-customer" not in p]
if "smoke" in sys.modules:
    del sys.modules["smoke"]
from smoke import (  # noqa: E402
    fake_embed,
    parse_table,
    search,
    table_to_text,
)

# ── parse_table:多格式 ──


def test_parse_simple_table():
    md = """| col1 | col2 |
|-------|------|
| a | b |
| c | d |
"""
    tables = parse_table(md)
    assert len(tables) == 1
    assert tables[0]["header"] == ["col1", "col2"]
    assert len(tables[0]["rows"]) == 2


def test_parse_multiple_tables():
    md = """| a | b |
|---|---|
| 1 | 2 |

paragraph

| x | y |
|---|---|
| 3 | 4 |
"""
    tables = parse_table(md)
    assert len(tables) == 2
    assert tables[0]["header"] == ["a", "b"]
    assert tables[1]["header"] == ["x", "y"]


def test_parse_no_table_returns_empty():
    """纯文本无表格 → 空 list。"""
    md = "This is just text.\nNo tables here.\n"
    assert parse_table(md) == []


def test_parse_empty_string():
    assert parse_table("") == []


def test_parse_table_with_separator_variants():
    """不同分隔符样式都能识别(:---、:---:、---:、---)。"""
    for sep in ["|---|", "|:---|", "|---:|", "|:---:|"]:
        md = f"| a | b |\n{sep}\n| 1 | 2 |\n"
        tables = parse_table(md)
        assert len(tables) == 1, f"failed for sep {sep!r}"
        assert tables[0]["header"] == ["a", "b"]


def test_parse_table_header_only_no_rows():
    """仅 header + 分隔行,无数据行。"""
    md = """| col1 | col2 |
|-------|------|
"""
    tables = parse_table(md)
    assert len(tables) == 1
    assert tables[0]["rows"] == []


def test_parse_table_strips_whitespace():
    """每个 cell 的前后空白被 strip 掉。"""
    md = """|  col1  |  col2  |
|--------|--------|
|  a  |  b  |
"""
    tables = parse_table(md)
    assert tables[0]["header"] == ["col1", "col2"]
    assert tables[0]["rows"][0] == ["a", "b"]


def test_parse_table_three_columns():
    """3 列也工作。"""
    md = """| a | b | c |
|---|---|---|
| 1 | 2 | 3 |
"""
    tables = parse_table(md)
    assert tables[0]["header"] == ["a", "b", "c"]
    assert tables[0]["rows"] == [["1", "2", "3"]]


# ── table_to_text ──


def test_table_to_text_includes_header_and_rows():
    table = {
        "header": ["col1", "col2"],
        "rows": [["a", "b"], ["c", "d"]],
    }
    text = table_to_text(table)
    lines = text.split("\n")
    assert len(lines) == 3
    assert "col1" in lines[0]
    assert "a" in lines[1]
    assert "c" in lines[2]


def test_table_to_text_empty_rows():
    """rows 为空 → 只输出 header 行。"""
    text = table_to_text({"header": ["a"], "rows": []})
    assert text == "a"


def test_table_to_text_uses_pipe_separator():
    """默认 " | " 分隔。"""
    text = table_to_text({"header": ["x", "y"], "rows": [["1", "2"]]})
    assert "x | y" in text
    assert "1 | 2" in text


# ── fake_embed ──


def test_fake_embed_shape_norm():
    """64 维 + 单位化(同 toy_embed)。"""
    v = fake_embed("hello world")
    assert v.shape == (64,)
    assert v.dtype == np.float32
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_fake_embed_same_text_same_vector():
    """确定性。"""
    a = fake_embed("foo bar")
    b = fake_embed("foo bar")
    assert np.allclose(a, b)


def test_fake_embed_different_text_different_vector():
    """不同词 → 不同分布(至少不全等)。"""
    a = fake_embed("苹果 营收")
    b = fake_embed("货币 政策")
    assert not np.allclose(a, b)


def test_fake_embed_empty_string():
    """空串 → 零向量(同 toy_embed 实现)。"""
    v = fake_embed("")
    assert v.shape == (64,)
    assert float(np.linalg.norm(v)) == 0.0


# ── search:排序 + 时间过滤 + 边界 ──


def test_time_filter():
    docs = [
        {"doc_id": "d1", "text": "苹果", "date": "2024-01-01"},
        {"doc_id": "d2", "text": "苹果", "date": "2024-12-01"},
    ]
    hits = search(docs, "苹果", k=2, before="2024-06-30")
    assert len(hits) == 1
    assert hits[0]["doc_id"] == "d1"


def test_search_no_time_filter():
    """before=None → 不过滤。"""
    docs = [
        {"doc_id": "d1", "text": "苹果", "date": "2024-01-01"},
        {"doc_id": "d2", "text": "苹果", "date": "2024-12-01"},
    ]
    hits = search(docs, "苹果", k=2)
    assert len(hits) == 2


def test_search_time_filter_excludes_all_when_out_of_range():
    """before 早于所有 doc → 空。"""
    docs = [
        {"doc_id": "d1", "text": "x", "date": "2024-12-01"},
        {"doc_id": "d2", "text": "y", "date": "2024-12-31"},
    ]
    hits = search(docs, "x", k=2, before="2024-01-01")
    assert hits == []


def test_search_time_filter_inclusive():
    """before 等于某 doc 日期 → 该 doc 被保留(<=)。"""
    docs = [{"doc_id": "d1", "text": "x", "date": "2024-06-30"}]
    hits = search(docs, "x", k=1, before="2024-06-30")
    assert len(hits) == 1


def test_search_k_zero_returns_empty():
    docs = [{"doc_id": "d1", "text": "x", "date": "2024-01-01"}]
    assert search(docs, "x", k=0) == []


def test_search_k_larger_than_corpus():
    docs = [
        {"doc_id": "d1", "text": "x", "date": "2024-01-01"},
        {"doc_id": "d2", "text": "y", "date": "2024-02-01"},
    ]
    hits = search(docs, "x", k=10)
    assert len(hits) == 2


def test_search_empty_corpus():
    assert search([], "anything", k=5) == []


def test_search_returns_top_k():
    """k=1 应返回 1 条;k=2 应返回 2 条。"""
    docs = [
        {"doc_id": f"d{i}", "text": f"term{i}", "date": "2024-01-01"}
        for i in range(5)
    ]
    assert len(search(docs, "term", k=1)) == 1
    assert len(search(docs, "term", k=2)) == 2
    assert len(search(docs, "term", k=5)) == 5


# ── 端到端 main() ──


def test_main_runs_on_fixtures(monkeypatch, tmp_path: Path):
    """main() 能在 fixtures 上跑完,生成 artifacts/P4/smoke.json。

    关键:测试函数内 importlib reload 防止 P3 的 smoke 模块污染 sys.modules。
    """
    # 在 chdir 之前先解析源路径(cwd-relative,避免 tmp_path 污染)
    src = Path("datasets/fixtures/finance").resolve()
    monkeypatch.chdir(tmp_path)
    dst = tmp_path / "datasets" / "fixtures" / "finance"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src)
    except OSError:
        import shutil

        shutil.copytree(src, dst)

    # 强制 reload P4 的 smoke(避免 P3 等其他项目的同名 smoke 残留)
    for k in [k for k in sys.modules if k == "smoke"]:
        del sys.modules[k]
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke", str(Path(__file__).resolve().parent.parent / "src" / "smoke.py")
    )
    smoke = importlib.util.module_from_spec(spec)
    sys.modules["smoke"] = smoke
    spec.loader.exec_module(smoke)

    smoke.main()
    out = tmp_path / "artifacts" / "P4" / "smoke.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["n_docs"] == 50  # fixtures 里有 50 条
    # 50 份年报至少应解析出几个表格(README 说有 markdown 表格)
    assert data["n_tables"] >= 1


def test_main_handles_missing_fixtures(monkeypatch, tmp_path, capsys):
    """fixtures 不存在时 main() 应优雅提示并 return,不应抛。"""
    monkeypatch.chdir(tmp_path)
    # 强制 reload P4 的 smoke
    for k in [k for k in sys.modules if k == "smoke"]:
        del sys.modules[k]
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "smoke", str(Path(__file__).resolve().parent.parent / "src" / "smoke.py")
    )
    smoke = importlib.util.module_from_spec(spec)
    sys.modules["smoke"] = smoke
    spec.loader.exec_module(smoke)

    smoke.main()  # 不抛
    captured = capsys.readouterr()
    assert "不存在" in captured.out or "⚠" in captured.out
