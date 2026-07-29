"""真正模拟 Tab 2/3 走的代码路径(不复用 verify_arch_fixture.py 的临时库):

  1) 真复现用户报错路径:P5ChromaStore + store.count() → 应不报错
  2) Tab 3 的 10 条 demo query 全跑一遍,确认 ≥ 1 命中
  3) 单元式测 _ensure_handle:mock 一个会抛 NotFoundError 的假 collection,
     验证自愈后 get_or_create 拿到的真实句柄替换上去

跑:
    ./.venv/bin/python scripts/verify_ui_tabs.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import chromadb.errors

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from src.chroma_store import P5ChromaStore  # noqa: E402
from src.loaders import load  # noqa: E402

DB = HERE / "chroma_db"  # 与 streamlit 同目录


def ollama_embed(texts: list[str]) -> list[list[float]]:
    out = []
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


def step1_count_after_rebuild() -> int:
    """Step 1: 真复现用户报错路径 + 入库全部 fixtures。
    之所以这里要重建 chroma_db:之前 rmtree 留下的空目录不会自我恢复,需要
    至少 add_document 一次才能让 count() 返回非零 —— 与 UI 首次进 Tab 2 一致。
    """
    import shutil

    if DB.exists() and not any(DB.iterdir()):
        shutil.rmtree(DB)  # 空目录清理,避免 chromadb 启动失败
    store = P5ChromaStore(DB, embed_fn=ollama_embed)
    for name in ["sample.pdf", "sample.docx", "sample.xlsx", "sample.html", "architecture.pdf"]:
        doc = load(HERE / "fixtures" / name)
        n_added = store.add_document(doc)
        print(f"  ✓ {name:22s}  blocks={len(doc.blocks):>2d}  added={n_added}")
    n = store.count()
    print(f"\n  count() = {n}  ← 用户报错路径,现已 OK")
    return n


def step2_tab3_demos() -> int:
    """Step 2: 跑 Tab 3 的 10 条 demo query,确认每条 ≥ 1 命中。"""
    print()
    print("═" * 60)
    print("Step 2: Tab 3 的 10 条 demo query")
    print("═" * 60)
    store = P5ChromaStore(DB, embed_fn=ollama_embed)
    demos = [
        "APAC region revenue chart 240M",
        "Regional growth rate APAC 32%",
        "laptop price 1499 catalog",
        "headcount engineering",
        "system architecture diagram components",
        "which database stores embeddings",
        "ingestion pipeline steps PDF",
        "rate limiter JWT authentication",
        "what reranker does retrieval use",
        "where does OpenTelemetry send traces",
    ]
    pass_count = 0
    for q in demos:
        hits = store.query(q, k=3)
        if hits:
            top = hits[0]
            kind = top["metadata"].get("block_type", "?")
            dist = top.get("distance", float("nan"))
            print(f'  ✓ [{kind:6s}] dist={dist:.3f}  "{q}"')
            pass_count += 1
        else:
            print(f'  ✗ 0 hits: "{q}"')
    print(f"\n  {pass_count}/{len(demos)} demos hit top-1")
    return pass_count


def step3_ensure_handle_self_heal() -> bool:
    """Step 3: 单元式测 _ensure_handle —— mock 假 collection 让它抛 NotFoundError,
    验证 store.collection 已被替换为真实新句柄。
    """
    print()
    print("═" * 60)
    print("Step 3: _ensure_handle 自愈 —— mock stale collection")
    print("═" * 60)
    store = P5ChromaStore(DB, embed_fn=ollama_embed)
    old_collection = store.collection
    old_id = old_collection.id
    print(f"  当前句柄 UUID = {old_id}")

    class _BrokenCollection:
        """完全没在 chromadb 注册过的假 collection,get() 必报 NotFoundError。"""

        name = "p5_multi_format"

        def get(self, *a, **kw):
            raise chromadb.errors.NotFoundError("Collection [fake-uuid] does not exist")

    # 把 store 的 collection 偷换成 broken 版本
    store.collection = _BrokenCollection()  # type: ignore[assignment]
    print("  注入 broken collection,现在调 _ensure_handle() ...")

    store._ensure_handle()

    new_collection = store.collection
    new_id = new_collection.id
    print(f"  自愈后句柄 UUID = {new_id}")
    assert new_id != "fake-uuid"
    # verify it actually works
    n = store.count()
    print(f"  ✓ 自愈后 store.count() = {n}")
    return True


def main() -> None:
    print("═" * 60)
    print("Step 1: 真复现 Tab 2 报错路径 — store.count()")
    print("═" * 60)
    n = step1_count_after_rebuild()
    assert n >= 20, f"入库太少:{n}"

    n_demos = step2_tab3_demos()
    assert n_demos == 10, f"Tab 3 demos {n_demos}/10"

    step3_ensure_handle_self_heal()

    print()
    print("═" * 60)
    print("✅ Tab 2/3 端到端 + _ensure_handle 自愈全部 PASS")
    print("═" * 60)


if __name__ == "__main__":
    main()
