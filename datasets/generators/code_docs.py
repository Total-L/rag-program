"""代码与技术文档语料生成器（基于任意 Python 包源码，默认 sample_corpus）。

用法：
    python -m datasets.generators.code_docs --out datasets/fixtures/code_docs
    python -m datasets.generators.code_docs --source /path/to/python/project

设计：
- 默认 source = `datasets/fixtures/code_docs/sample_corpus/`（仓库自带 11 个示例 .py）
- 可通过 `--source` 指向任意 Python 项目目录
- 提取 module-level docstring + class/function docstring
- AST 解析：函数签名 + 简要说明
- 输出 JSONL：每行一个 doc-unit（module/class/function）

注：本生成器不再硬编码 `/Users/totallai/rag-course/kbchat` 路径
（ADR-0005：P2 自包含，不依赖 rag-course 存在）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 缺省 source = 仓库自带 sample_corpus（11 个示例 .py）
DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "fixtures" / "code_docs" / "sample_corpus"


@dataclass
class CodeDoc:
    doc_id: str
    package: str
    file: str
    kind: str          # module | class | function | method
    name: str
    signature: str
    docstring: str
    source_excerpt: str
    lineno: int


def _safe_doc(node: ast.AST) -> str:
    return ast.get_docstring(node) or ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return ast.unparse(node.args)  # type: ignore[attr-defined]
    except Exception:
        return "(...)"


def _excerpt(source: str, lineno: int, max_lines: int = 20) -> str:
    lines = source.splitlines()
    start = max(0, lineno - 1)
    end = min(len(lines), start + max_lines)
    return "\n".join(lines[start:end])


def parse_file(path: Path, package_root: str) -> list[CodeDoc]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    pkg = package_root
    docs: list[CodeDoc] = []
    rel = str(path.relative_to(package_root))
    # module 级
    docs.append(CodeDoc(
        doc_id=f"{pkg}/{rel}#module",
        package=pkg, file=rel, kind="module",
        name=path.stem, signature="",
        docstring=_safe_doc(tree), source_excerpt="", lineno=0,
    ))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            docs.append(CodeDoc(
                doc_id=f"{pkg}/{rel}#{node.name}",
                package=pkg, file=rel, kind="class",
                name=node.name, signature="",
                docstring=_safe_doc(node),
                source_excerpt=_excerpt(text, node.lineno),
                lineno=node.lineno,
            ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if any(isinstance(p, ast.ClassDef) for p in ast.walk(tree) if isinstance(p, ast.ClassDef) and any(c is node for c in ast.walk(p))) else "function"
            docs.append(CodeDoc(
                doc_id=f"{pkg}/{rel}#{node.name}:{node.lineno}",
                package=pkg, file=rel, kind=kind,
                name=node.name, signature=_signature(node),
                docstring=_safe_doc(node),
                source_excerpt=_excerpt(text, node.lineno),
                lineno=node.lineno,
            ))
    return docs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="datasets/fixtures/code_docs")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="要解析的 Python 项目根目录（默认 = 仓库自带 sample_corpus）",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pkg_root = Path(args.source)
    if not pkg_root.exists():
        raise SystemExit(f"--source {pkg_root} 不存在，请指定一个有效的 Python 项目目录")

    all_docs: list[CodeDoc] = []
    for py in pkg_root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        all_docs.extend(parse_file(py, pkg_root))

    (out / "code_docs.jsonl").write_text(
        "\n".join(json.dumps(asdict(d), ensure_ascii=False) for d in all_docs) + "\n",
        encoding="utf-8",
    )
    print(f"✓ generated {len(all_docs)} code-doc units from {pkg_root} → {out}/code_docs.jsonl")


if __name__ == "__main__":
    main()
