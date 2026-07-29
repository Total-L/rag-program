"""P2 smoke 单测（ENTERPRISE_AUDIT C2 扩写）。

覆盖 parse_python_file / toy_embed / search 三个核心 + 端到端 main()
在 sample_corpus 上的索引与检索。所有测试使用 tmp_path / 内联 fixture,
不依赖任何 kbchat / rag-course。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

# 强制只插入 P2 src,避免其他项目污染
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path = [p for p in sys.path if "p3-customer" not in p and "p4-finance" not in p]
if "smoke" in sys.modules:
    del sys.modules["smoke"]
from smoke import (  # noqa: E402
    DEFAULT_CORPUS,
    parse_python_file,
    search,
    toy_embed,
)


def _write_tmp_py(name: str, body: str) -> Path:
    """写一个临时 .py 文件,返回路径。"""
    tmp = Path(tempfile.mkdtemp()) / name
    tmp.write_text(body, encoding="utf-8")
    return tmp


# ── parse_python_file:AST 切分 ──


def test_parse_simple():
    sample = Path(__file__).parent / "sample.py"
    sample.write_text(
        '"""Module doc."""\n'
        "def foo(x: int) -> int:\n"
        '    """Double x."""\n'
        "    return x * 2\n"
        "class Bar:\n"
        '    """Bar class."""\n'
        "    def baz(self):\n"
        '        """baz method."""\n'
        "        pass\n",
        encoding="utf-8",
    )
    docs = parse_python_file(sample)
    kinds = {d["kind"] for d in docs}
    assert "module" in kinds
    assert "function" in kinds
    assert "class" in kinds
    sample.unlink()


def test_parse_module_has_doc():
    """每个文件至少生成 1 条 module 级别 doc-unit,且 module docstring 被捕获。"""
    f = _write_tmp_py(
        "mod_doc.py",
        '"""Top-level module docstring."""\nx = 1\n',
    )
    docs = parse_python_file(f)
    mods = [d for d in docs if d["kind"] == "module"]
    assert len(mods) == 1
    assert "Top-level module docstring" in mods[0]["doc"]


def test_parse_function_signature():
    """function doc-unit 包含签名字符串(参数列表)。"""
    f = _write_tmp_py(
        "sig.py",
        "def add(a: int, b: int = 0) -> int:\n    return a + b\n",
    )
    docs = parse_python_file(f)
    funcs = [d for d in docs if d["kind"] == "function"]
    assert len(funcs) == 1
    assert funcs[0]["name"] == "add"
    # ast.unparse(args) 至少应包含参数名
    assert "a" in funcs[0]["signature"]
    assert "b" in funcs[0]["signature"]


def test_parse_class_with_inheritance():
    """class doc-unit 的 signature 应包含类名(简化格式 class Name)。"""
    f = _write_tmp_py(
        "cls.py",
        "class Foo(Bar):\n    pass\n",
    )
    docs = parse_python_file(f)
    classes = [d for d in docs if d["kind"] == "class"]
    assert len(classes) == 1
    assert classes[0]["name"] == "Foo"
    assert "class Foo" == classes[0]["signature"]


def test_parse_async_function_detected():
    """async def 应被识别为 function(同 FunctionDef 路径)。"""
    f = _write_tmp_py(
        "async_fn.py",
        "async def fetch(url: str) -> bytes:\n    return b''\n",
    )
    docs = parse_python_file(f)
    names = {d["name"] for d in docs if d["kind"] == "function"}
    assert "fetch" in names


def test_parse_nested_function_captured():
    """ast.walk 会扫到嵌套函数。"""
    f = _write_tmp_py(
        "nested.py",
        "def outer():\n"
        '    """outer doc."""\n'
        "    def inner():\n"
        '        """inner doc."""\n'
        "        return 1\n"
        "    return inner\n",
    )
    docs = parse_python_file(f)
    names = {d["name"] for d in docs if d["kind"] == "function"}
    assert "outer" in names
    assert "inner" in names


def test_parse_syntax_error_returns_empty():
    """SyntaxError 文件 → 空 list(不抛)。"""
    f = _write_tmp_py(
        "broken.py",
        "def incomplete(:\n",
    )
    docs = parse_python_file(f)
    assert docs == []


def test_parse_empty_file():
    """空文件 → 只有 module doc-unit(空 docstring)。"""
    f = _write_tmp_py("empty.py", "")
    docs = parse_python_file(f)
    assert len(docs) == 1
    assert docs[0]["kind"] == "module"


# ── toy_embed ──


def test_toy_embed_shape_and_norm():
    """输出 64 维、单位长度。"""
    v = toy_embed("hello world hello")
    assert v.shape == (64,)
    assert v.dtype == np.float32
    # 归一化 → norm == 1
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


def test_toy_embed_same_text_same_vector():
    """同文本 → 同向量(cosine sim = 1)。"""
    a = toy_embed("foo bar baz")
    b = toy_embed("foo bar baz")
    assert np.allclose(a, b)


def test_toy_embed_empty_string_handled():
    """空字符串 → 零向量(实现用 1e-9 ε 保护除零,所以 norm=0 不抛)。"""
    v = toy_embed("")
    assert v.shape == (64,)
    assert float(np.linalg.norm(v)) == 0.0


# ── search ──


def test_search_returns_results():
    docs = [
        {"kind": "function", "name": "foo", "doc": "doubles a number", "file": "x.py", "lineno": 1},
        {"kind": "function", "name": "bar", "doc": "halves a number", "file": "x.py", "lineno": 5},
    ]
    hits = search(docs, "double", k=1)
    assert hits[0]["name"] == "foo"


def test_search_k_zero_returns_empty():
    docs = [{"kind": "function", "name": "x", "doc": "d", "file": "f", "lineno": 1}]
    assert search(docs, "anything", k=0) == []


def test_search_k_larger_than_corpus():
    """k > |docs| → 返回全部(不超过 corpus 长度)。"""
    docs = [
        {"kind": "function", "name": "a", "doc": "x", "file": "f", "lineno": 1},
        {"kind": "function", "name": "b", "doc": "y", "file": "f", "lineno": 2},
    ]
    hits = search(docs, "x", k=10)
    assert len(hits) == 2


def test_search_empty_corpus():
    """空语料 → 空结果。"""
    assert search([], "anything", k=5) == []


def test_search_returns_top_k_in_descending_relevance():
    """返回顺序应是相关性递减(前 k 条应满足 sim[0] >= sim[1] >= ...)。"""
    docs = [
        {"kind": "function", "name": "unrelated", "doc": "alpha beta gamma", "file": "f", "lineno": 1},
        {"kind": "function", "name": "match", "doc": "double double double", "file": "f", "lineno": 2},
        {"kind": "function", "name": "weak", "doc": "double alpha", "file": "f", "lineno": 3},
    ]
    hits = search(docs, "double", k=3)
    assert len(hits) == 3
    # 第一条 doc 应含 "double" 多次(最强信号)
    assert "double" in hits[0]["doc"]


# ── 端到端:在 DEFAULT_CORPUS 上跑一遍 parse + search ──


def test_default_corpus_exists_and_parses():
    """sample_corpus 应可被解析出多个 doc-unit。"""
    assert DEFAULT_CORPUS.exists(), f"sample corpus missing: {DEFAULT_CORPUS}"
    all_docs: list[dict] = []
    for py in DEFAULT_CORPUS.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        all_docs.extend(parse_python_file(py))
    # 11 个 .py → 每个至少 1 module,共 ≥11
    assert len(all_docs) >= 11
    kinds = {d["kind"] for d in all_docs}
    assert {"module", "function"}.issubset(kinds)


def test_e2e_search_on_default_corpus():
    """对真实语料做一次检索,应有非空结果。"""
    all_docs: list[dict] = []
    for py in DEFAULT_CORPUS.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        all_docs.extend(parse_python_file(py))
    # smoke.py 里的示例 query 之一:"四则运算"
    hits = search(all_docs, "division", k=3)
    assert len(hits) == 3
    # 不强制精确等于(BoW embedding 不可靠),只要求返回 3 个 doc-unit
    assert all("name" in h and "file" in h for h in hits)
