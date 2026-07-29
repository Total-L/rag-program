"""P5 Chroma 写路径回归测试 —— 审计发现的两条硬伤。

原来的问题：
1. `add_document` 是 append-only + skip-if-exists
   → 同 id 内容变了写不进去（静默丢更新）；文档编辑后旧 block 永久残留
2. `embed_fn=None` 静默用零向量
   → cosine 全部相等，检索"看起来能跑"但完全无意义，极难排查

顺带修掉的 O(N²)：原来每篇文档都把**全库 id** 拉进内存做去重。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.chroma_store import P5ChromaStore  # noqa: E402
from src.models import Block, BlockType, Document, SourceFormat  # noqa: E402


def _embed(texts: list[str]) -> list[list[float]]:
    """确定性假嵌入（词哈希）。测流程用，不测检索质量。"""
    out = []
    for t in texts:
        v = [0.0] * 16
        for tok in t.split():
            v[hash(tok) % 16] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


def _block(path: str, bid: str, text: str, page: str = "p1") -> Block:
    return Block(
        block_id=bid,
        source_path=path,
        source_format=SourceFormat.MD,
        block_type=BlockType.TEXT,
        text=text,
        page_or_sheet=page,
    )


def _doc(path: str, blocks: list[tuple[str, str]], page: str = "p1") -> Document:
    """造一份 Document。blocks = [(block_id, text)]

    注意 `Block` 是 frozen dataclass，改字段要重建对象。
    另外 `content_hash` **不含 block_id**（含 source_path+page+type+text），
    所以同一文档里两个同文本 block 会撞 id —— 这正是要测的去重场景。
    """
    return Document(
        source_path=path,
        source_format=SourceFormat.MD,
        blocks=tuple(_block(path, bid, text, page) for bid, text in blocks),
        parse_warnings=(),
    )


@pytest.fixture()
def store(tmp_path: Path) -> P5ChromaStore:
    return P5ChromaStore(tmp_path / "chroma_db", embed_fn=_embed)


# ── ① upsert 幂等 ────────────────────────────────────
def test_add_document_is_idempotent(store: P5ChromaStore):
    doc = _doc("/tmp/a.md", [("b1", "alpha beta"), ("b2", "gamma delta")])
    assert store.add_document(doc) == 2
    n = store.count()
    assert n == 2
    assert store.add_document(doc) == 0, "重复写入不该报告新增"
    assert store.count() == n, "重跑不该制造重复行"


def test_upsert_updates_metadata_in_place(store: P5ChromaStore):
    """同 content_hash 重新写入时 metadata 也应更新（原来整条被 skip 掉）。

    这里改 `block_id`（它进 chroma metadata 但**不参与** content_hash），
    所以 id 不变、metadata 变 —— 正好验证"同 id 的更新能不能写进去"。
    """
    doc = _doc("/tmp/a.md", [("b1", "alpha beta")])
    store.add_document(doc)
    cid = doc.blocks[0].content_hash

    doc2 = _doc("/tmp/a.md", [("b1-renamed", "alpha beta")])
    assert doc2.blocks[0].content_hash == cid, "content_hash 不该受 block_id 影响"
    store.add_document(doc2)

    got = store.collection.get(ids=[cid], include=["metadatas"])
    assert got["metadatas"][0]["block_id"] == "b1-renamed", "同 id 的更新必须生效"
    assert store.count() == 1


# ── ② 对账清僵尸 block ────────────────────────────────
def test_reconcile_removes_stale_blocks(store: P5ChromaStore):
    """★ 文档被编辑后不留僵尸向量。"""
    doc = _doc("/tmp/a.md", [("b1", "alpha beta"), ("b2", "gamma delta")])
    store.add_document(doc)
    assert store.count() == 2

    # 编辑第二个 block → content_hash 变 → 新 id
    edited = _doc("/tmp/a.md", [("b1", "alpha beta"), ("b2", "gamma delta EDITED")])
    store.add_document(edited)
    assert store.count() == 3, "此刻旧 block 还在 = 僵尸向量"

    removed = store.reconcile_source("/tmp/a.md", [b.content_hash for b in edited.blocks])
    assert removed == 1
    assert store.count() == 2
    ids = set(store.collection.get(include=[])["ids"])
    assert ids == {b.content_hash for b in edited.blocks}


def test_reconcile_does_not_touch_other_sources(store: P5ChromaStore):
    a = _doc("/tmp/a.md", [("b1", "alpha")])
    b = _doc("/tmp/b.md", [("b2", "beta")])
    store.add_document(a)
    store.add_document(b)
    store.reconcile_source("/tmp/a.md", [])
    assert store.count() == 1, "只该删 a.md 的 block"


# ── ③ 按源删除 ───────────────────────────────────────
def test_delete_by_source(store: P5ChromaStore):
    store.add_document(_doc("/tmp/a.md", [("b1", "alpha")]))
    store.add_document(_doc("/tmp/b.md", [("b2", "beta")]))
    assert store.delete_by_source("/tmp/b.md") == 1
    assert store.count() == 1


# ── ④ 零向量必须大声告警 ──────────────────────────────
def test_zero_vector_default_warns_loudly(tmp_path, capsys):
    """★ 不能静默：零向量会让检索结果无意义且极难排查。"""
    with pytest.warns(RuntimeWarning, match="零向量"):
        P5ChromaStore(tmp_path / "db2")
    out = capsys.readouterr().out
    assert "零向量" in out, "除了 warning 还要在 stdout 打眼提示"


def test_explicit_embed_fn_does_not_warn(tmp_path, recwarn):
    P5ChromaStore(tmp_path / "db3", embed_fn=_embed)
    assert not [w for w in recwarn if "零向量" in str(w.message)]


# ── ⑤ 存在性检查不再是 O(全库) ─────────────────────────
def test_existence_check_is_scoped_to_document(store: P5ChromaStore, monkeypatch):
    """回归 O(N²)：get() 必须带 ids 参数，不能全库扫。"""
    store.add_document(_doc("/tmp/a.md", [("b1", "alpha")]))

    calls: list[dict] = []
    real_get = store.collection.get

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real_get(*args, **kwargs)

    monkeypatch.setattr(store.collection, "get", spy)
    store.add_document(_doc("/tmp/b.md", [("b2", "beta")]))

    assert calls, "应有存在性查询"
    assert any("ids" in c and c["ids"] for c in calls), (
        "存在性检查必须按 ids 查（O(本文档)），不能拉全库"
    )


def test_duplicate_blocks_within_one_doc_are_deduped(store: P5ChromaStore):
    """同一文档里两个完全相同的 block（同 content_hash）不能让 upsert 报错。"""
    doc = _doc("/tmp/a.md", [("b1", "same text"), ("b2", "same text")])
    assert doc.blocks[0].content_hash == doc.blocks[1].content_hash
    store.add_document(doc)
    assert store.count() == 1
