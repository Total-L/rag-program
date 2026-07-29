"""P5 Chroma 烟雾测试：用 Ollama nomic-embed-text 跑真语义检索。

跑：
    python projects/p5-multi-format-loader/scripts/smoke_chroma.py            # 默认:追加,不去重主库
    python projects/p5-multi-format-loader/scripts/smoke_chroma.py --reset    # 抹掉主库再跑(危险,UI 会丢库)
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.chroma_store import P5ChromaStore  # noqa: E402
from src.loaders import load  # noqa: E402
from src.models import BlockType  # noqa: E402


def ollama_embed(texts: list[str]) -> list[list[float]]:
    """调 Ollama embed API。"""
    out: list[list[float]] = []
    for t in texts:
        body = json.dumps({"model": "nomic-embed-text", "prompt": t}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            out.append(json.loads(r.read())["embedding"])
    return out


def main() -> None:
    fix = HERE.parent / "fixtures"
    db_dir = HERE.parent / "chroma_db"

    # 默认不抹主库 —— streamlit UI 还在用它,抹掉会让 UI 报 NotFoundError
    # 只有显式 --reset 才抹(此时 UI 必须清缓存重连)
    reset = "--reset" in sys.argv
    if reset:
        print("⚠️  --reset 模式:抹掉 chroma_db 后再跑")
        if db_dir.exists():
            shutil.rmtree(db_dir)
    elif db_dir.exists():
        print(
            f"→ 追加模式:已有 chroma_db ({sum(p.stat().st_size for p in db_dir.rglob('*') if p.is_file()):,} B),"
            f"按 content_hash 去重"
        )
    # 用 Ollama 嵌入(已经在跑)
    store = P5ChromaStore(db_dir, embed_fn=ollama_embed)
    print(f"  connected to collection UUID={store.collection.id}")
    files = ["sample.pdf", "sample.docx", "sample.xlsx", "sample.html"]
    for f in files:
        doc = load(fix / f)
        n_added = store.add_document(doc)
        types = {b.block_type.value: 0 for b in doc.blocks}
        for b in doc.blocks:
            types[b.block_type.value] += 1
        print(f"  ✓ {f:12s}  blocks={len(doc.blocks):>2d}  types={types}  added={n_added}")

    print(f"\n  Chroma 总记录: {store.count()}")
    print("\n=== 语义检索（Ollama 嵌入） ===")
    for q, where in [
        ("What was APAC revenue?", None),
        ("new hire first week tasks", {"source_format": "docx"}),
        ("Engineering team size Q3", {"source_format": "xlsx"}),
        ("laptop product image", None),
    ]:
        results = store.query(q, k=3, where=where)
        print(f"\n  Q: {q}")
        for r in results[:3]:
            mt = r["metadata"]
            print(
                f"    [{mt['block_type']:7s}] dist={r['distance']:.3f}  "
                f"file={Path(mt['source_path']).name}  "
                f"text={r['text'][:55]!r}".replace(chr(10), " ")
            )

    print("\n=== block_type=image 过滤 ===")
    images = store.filter_by_type(BlockType.IMAGE)
    for img in images:
        print(f"  {img['metadata']['source_format']:6s}  {img['text']!r}")


if __name__ == "__main__":
    main()
