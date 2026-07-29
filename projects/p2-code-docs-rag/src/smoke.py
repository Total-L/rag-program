"""P2 — Code & Tech Docs 烟雾测试。

纯 NumPy + AST 切分；无 kbchat 依赖。
默认语料：`datasets/fixtures/code_docs/sample_corpus/`（10+ 示例 Python 源文件）。
可通过 `P2_CODE_TARGET` 环境变量指定任意 Python 项目目录。
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS = PROJECT_ROOT / "datasets" / "fixtures" / "code_docs" / "sample_corpus"


def parse_python_file(path: Path) -> list[dict]:
    """解析 .py 文件：module/class/function doc-units。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    docs: list[dict] = []
    rel = str(path)
    docs.append(
        {
            "kind": "module",
            "name": path.stem,
            "doc": ast.get_docstring(tree) or "",
            "file": rel,
            "lineno": 0,
        }
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            docs.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "doc": ast.get_docstring(node) or "",
                    "file": rel,
                    "lineno": node.lineno,
                    "signature": f"class {node.name}",
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                sig = ast.unparse(node.args)
            except Exception:
                sig = "(...)"
            docs.append(
                {
                    "kind": "function",
                    "name": node.name,
                    "doc": ast.get_docstring(node) or "",
                    "file": rel,
                    "lineno": node.lineno,
                    "signature": sig,
                }
            )
    return docs


def toy_embed(text: str) -> np.ndarray:
    """toy bag-of-words embedding（演示）。"""
    v = np.zeros(64, dtype=np.float32)
    for w in text.split():
        v[hash(w) % 64] += 1
    return v / max(np.linalg.norm(v), 1e-9)


def search(docs: list[dict], query: str, k: int = 3) -> list[dict]:
    qv = toy_embed(query)
    sims = []
    for d in docs:
        dv = toy_embed(d["doc"] + " " + d["name"])
        sims.append((d, float(dv @ qv)))
    sims.sort(key=lambda x: -x[1])
    return [d for d, _ in sims[:k]]


def main() -> None:
    print("════════════════════════════════════════════")
    print("  P2 — Code & Tech Docs 烟雾测试")
    print("════════════════════════════════════════════\n")

    # 解析语料目录（默认 sample_corpus，可被 P2_CODE_TARGET 覆盖）
    target = Path(os.environ.get("P2_CODE_TARGET", str(DEFAULT_CORPUS)))
    if not target.exists():
        print(f"⚠ {target} 不存在，请设置 P2_CODE_TARGET 指向 Python 项目目录")
        return

    all_docs: list[dict] = []
    for py in target.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        all_docs.extend(parse_python_file(py))
    print(f"→ 解析 {len(all_docs)} 个 doc-units（module/class/function）")

    # 检索演示
    queries = [
        "how to load an Obsidian vault",
        "build grounded prompt",
        "pack evidence by token budget",
    ]
    for q in queries:
        print(f"\nQ: {q}")
        hits = search(all_docs, q, k=3)
        for h in hits:
            print(f"  [{h['kind']:8s}] {h['name']:30s} ({Path(h['file']).name}:{h['lineno']})")

    # 写结果
    out = Path("artifacts/P2/smoke.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "n_docs": len(all_docs),
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ P2 完成 → {out}")


if __name__ == "__main__":
    main()
