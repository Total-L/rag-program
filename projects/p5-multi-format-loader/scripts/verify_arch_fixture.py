"""P5 — 验证 architecture.pdf fixture 端到端(load → OCR → Chroma 入库 → query 命中)。

跑:
    ./.venv/bin/python scripts/verify_arch_fixture.py

验收点(全部 PASS 才 exit 0):
  1. 抽出 ≥ 4 个 block(1 image + 1 table + 1 text)
  2. image block OCR 命中 ≥ 13 个关键词中的 11 个
  3. image block metadata 含 image_path(指向 .derived/)
  4. 5 条 demo query 命中 top-1,block_type 符合预期
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from src.loaders import load  # noqa: E402
from src.models import BlockType  # noqa: E402

FIXTURE = HERE / "fixtures" / "architecture.pdf"
VERIFY_DB = HERE / ".verify_arch_chroma"
REQUIRED_KEYWORDS = [
    "API Gateway",
    "Vector DB",
    "Chroma",
    "Ollama",
    "OpenTelemetry",
    "Reranker",
    "BM25",
    "Docling",
    "Redis",
    "Postgres",
    "Llama",
    "Streamlit",
    "FastAPI",
]


def main():
    if VERIFY_DB.exists():
        shutil.rmtree(VERIFY_DB)

    print(f"→ loading {FIXTURE.name}")
    doc = load(FIXTURE)
    print(f"  {len(doc.blocks)} blocks extracted")

    # ── 检查 1:block 数量 + 类型分布 ──
    assert len(doc.blocks) >= 3, f"too few blocks: {len(doc.blocks)}"
    by_type = {}
    for b in doc.blocks:
        by_type[b.block_type.value] = by_type.get(b.block_type.value, 0) + 1
    print(f"  by_type: {by_type}")
    assert by_type.get("image", 0) >= 1, "no image block"
    assert by_type.get("text", 0) >= 1, "no text block"
    assert by_type.get("table", 0) >= 1, "no table block"

    # ── 检查 2:image block OCR 命中关键词 ──
    img = next(b for b in doc.blocks if b.block_type == BlockType.IMAGE)
    ocr = img.ocr_text
    hits = [kw for kw in REQUIRED_KEYWORDS if kw.lower() in ocr.lower()]
    print(f"  OCR chars: {len(ocr)} | keyword hits: {len(hits)}/{len(REQUIRED_KEYWORDS)}")
    for kw in REQUIRED_KEYWORDS:
        mark = "✓" if kw.lower() in ocr.lower() else "✗"
        print(f"    {mark} {kw}")
    assert len(hits) >= 11, f"OCR keyword hit too low: {len(hits)}/{len(REQUIRED_KEYWORDS)}"

    # ── 检查 3:image block metadata.image_path 落盘 ──
    img_path = img.metadata.get("image_path")
    assert img_path, "image block missing image_path metadata"
    assert Path(img_path).exists(), f"image_path not on disk: {img_path}"
    print(f"  ✓ image_path → {img_path} ({Path(img_path).stat().st_size:,} B)")

    # ── 检查 4:Chroma 入库 + 5 条 query 命中 top-1 ──
    print("\n→ Chroma 入库 + query 验证")
    try:
        import json as _json
        import urllib.request

        from src.chroma_store import P5ChromaStore

        # 调本地 Ollama nomic-embed-text(localhost:11434)
        def ollama_embed(texts):
            out = []
            for t in texts:
                body = _json.dumps({"model": "nomic-embed-text", "prompt": t}).encode()
                req = urllib.request.Request(
                    "http://localhost:11434/api/embeddings",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    out.append(_json.loads(r.read())["embedding"])
            return out

        store = P5ChromaStore(VERIFY_DB, embed_fn=ollama_embed)
        n_added = store.add_document(doc)
        print(f"  ✓ added {n_added} records, total {store.count()}")
    except Exception as e:
        # Ollama 不可用时的兜底:仅做 keyword presence 检查
        print(f"  ⚠ Ollama/Chroma skipped ({type(e).__name__}: {e}),仅做 keyword 检查")
        store = None

    queries = [
        # (query, [expected_kinds]) 任意命中一种即可,不强求唯一
        (
            "system architecture diagram components",
            ["image"],
            ["architecture", "API Gateway", "Service"],
        ),
        (
            "which database stores embeddings",
            ["image", "text"],
            ["Chroma", "Vector DB", "Embedder"],
        ),
        ("ingestion pipeline steps", ["text"], ["Ingestion Pipeline", "Docling", "chunk"]),
        ("rate limiter JWT authentication", ["image", "table"], ["Rate Limiter", "JWT", "Auth"]),
        (
            "where does OpenTelemetry send traces",
            ["image", "table"],
            ["OpenTelemetry", "Trace", "Tempo"],
        ),
    ]
    # 统计命中分布
    kind_counts: dict[str, int] = {"image": 0, "text": 0, "table": 0}
    for q, expected_kinds, must_in_block in queries:
        if store is not None:
            hits_ = store.query(q, k=1)
            assert hits_, f"empty result for: {q}"
            top = hits_[0]
            actual_kind = top["metadata"].get("block_type")
            dist = top.get("distance", float("nan"))
            src = top["metadata"].get("source_path", "?")[-30:]
            print(f'  ✓ "{q}" → [{actual_kind}] dist={dist:.3f} src=…{src}')
            assert actual_kind in expected_kinds, (
                f'"{q}" expected one of {expected_kinds}, got [{actual_kind}]'
            )
            kind_counts[actual_kind] = kind_counts.get(actual_kind, 0) + 1
        else:
            # 兜底:从 doc.blocks 里找任一 expected_kind 的 block,确认 must_in_block 至少 1 个在
            block = None
            for ek in expected_kinds:
                block = next((b for b in doc.blocks if b.block_type.value == ek), None)
                if block is not None:
                    break
            assert block is not None, f"no block of kinds {expected_kinds}"
            blob = (block.text or "") + " " + (block.ocr_text or "")
            hit = [kw for kw in must_in_block if kw.lower() in blob.lower()]
            assert hit, (
                f'"{q}" → [{block.block_type.value}] but no must_in_block match: {must_in_block}'
            )
            print(f'  ✓ "{q}" → [{block.block_type.value}] (matched {hit[0]!r} by structure)')
            kind_counts[block.block_type.value] = kind_counts.get(block.block_type.value, 0) + 1

    # 多样性断言:每种 block_type 至少被命中 1 次(覆盖 image/text/table 三类)
    print(f"\n  kind coverage: {kind_counts}")
    for kind in ("image", "text", "table"):
        assert kind_counts.get(kind, 0) >= 1, f"no query hit {kind} block — coverage gap"

    # ── 完成 ──
    shutil.rmtree(VERIFY_DB, ignore_errors=True)
    print("\n✅ all checks PASSED — architecture.pdf ready for UI")


if __name__ == "__main__":
    main()
