"""P5 — Chroma 向量库写入与查询。

设计原则：
- 每个 Block（text/table/heading/image）独立一条记录
- 文档用 markdown 文本（表格已转 markdown）embedding
- metadata 包含 block_type / source_format / page_or_sheet / headers（用 | 分隔）/ content_hash
- 用 content_hash 去重 → 同一 Block 多次 add 不增记录
- 嵌入式 Chroma（PersistentClient）写到本地目录，重启可恢复

为什么 metadata 必须可序列化（不能 list/nested dict）：
- Chroma 只支持 str/int/float/bool
- headers 用 "|" join 成一字符串存
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from .models import BlockType, Document

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "p5_multi_format"


def _sanitize_meta(md: dict[str, Any]) -> dict[str, Any]:
    """保证 Chroma metadata 只含 str/int/float/bool。"""
    out: dict[str, Any] = {}
    for k, v in md.items():
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = "|".join(str(x) for x in v) if v else ""
        else:
            out[k] = str(v)
    return out


class P5ChromaStore:
    """P5 的 Chroma 封装 —— 写入 + 查询 + 去重。

    默认用 Chroma 内置 embedding（下载慢）。可以传 embed_fn 改用本地
    Ollama / 自定义模型。embed_fn: list[str] -> list[list[float]]。
    """

    def __init__(
        self,
        path: Path | str,
        collection_name: str = DEFAULT_COLLECTION,
        embed_fn=None,
    ):
        self.path = Path(path).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(self.path),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        # embedding 函数：None = 用**零向量假函数**（仅验证写入/读取流程，检索无意义）
        # 传 embed_fn 用本地 Ollama / 自定义模型 → 才是真实可检索的向量
        from chromadb.utils import embedding_functions

        if embed_fn is None:
            # ⚠️ 必须大声告警：静默零向量会让 cosine 全部相等，
            # 检索结果看起来"能跑"但完全无意义 —— 这类误会极难排查。
            log.warning(
                "P5ChromaStore 未传 embed_fn → 使用零向量假 embedding。"
                "写入/读取流程可验证，但**检索结果无意义**。生产/评测请传真实 embed_fn。"
            )
            warnings.warn(
                "P5ChromaStore(embed_fn=None) 使用零向量假 embedding，检索无意义。"
                "请传 embed_fn（如本地 Ollama nomic-embed-text）。",
                RuntimeWarning,
                stacklevel=2,
            )
            print("⚠️  P5ChromaStore: embed_fn=None → 零向量假 embedding，检索结果无意义。")

            class _ZeroEF(embedding_functions.EmbeddingFunction):
                def __call__(self, input):
                    return [[0.0] * 384 for _ in input]

            ef = _ZeroEF()
        else:

            class _CustomEF(embedding_functions.EmbeddingFunction):
                def __init__(self, fn):
                    self.fn = fn

                def __call__(self, input):
                    return self.fn(list(input))

            ef = _CustomEF(embed_fn)
        self.embed_fn = embed_fn  # 留个引用方便查询时复用
        self._ef = ef  # 句柄自愈时需要用同名 EF 重建
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=ef,
        )

    # ── 句柄自愈 ───────────────────────────────────
    def _ensure_handle(self) -> None:
        """self.collection 指向的 UUID 可能被外部脚本(rmtree + 重建)作废。
        用最便宜的 API(get include=[])探活,NotFoundError 则按同名重新拿句柄。
        重建用初始化的同一 EF,embed 维度/模型保持一致。
        """
        try:
            _ = self.collection.get(include=[])
        except chromadb.errors.NotFoundError:
            old_id = getattr(self.collection, "id", "?")
            log.warning(
                "chroma collection %r stale (UUID=%s), reconnecting",
                self.collection.name,
                old_id,
            )
            self.collection = self.client.get_or_create_collection(
                name=self.collection.name,
                metadata={"hnsw:space": "cosine"},
                embedding_function=self._ef,
            )

    # ── 写入 ─────────────────────────────────────────
    def add_document(self, doc: Document) -> int:
        """把一份 Document 的所有 Block 写入（**upsert 语义**）。返回"新增"条数。

        为什么从 `add()` 改成 `upsert()`：
        原来是 append-only + skip-if-exists，会有两个后果 ——
        1. 同一 block_id 内容变了写不进去（静默丢更新）
        2. 文档被编辑后旧 block 永远留在库里（僵尸向量，污染召回）
        upsert 让写入天然幂等；删除失效 block 交给 `reconcile_source()`。

        顺带修掉一个 O(N²)：原来每篇文档都 `collection.get(include=[])` 把**全库
        id** 拉进内存做去重判断，语料一大就慢到不可用。现在只按本文档的 id 查存在性
        （`get(ids=...)`），复杂度从 O(全库) 降到 O(本文档 block 数)。
        """
        self._ensure_handle()  # 句柄可能 stale(外部脚本重建过 chroma_db)
        if not doc.blocks:
            return 0
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        seen: set[str] = set()
        for blk in doc.blocks:
            bid = blk.content_hash
            if bid in seen:
                continue  # 同一文档内重复 block（upsert 不允许同批重复 id）
            seen.add(bid)
            text_for_embed = blk.text or blk.ocr_text or blk.image_caption or ""
            if not text_for_embed and blk.block_type == BlockType.IMAGE:
                text_for_embed = f"[image at {blk.page_or_sheet}]"
            ids.append(bid)
            docs.append(text_for_embed)
            metas.append(_sanitize_meta(blk.to_chroma_metadata()))
        if not ids:
            return 0
        # 只查本文档这批 id 的存在性（O(本文档)，不是 O(全库)）
        already = set(self.collection.get(ids=ids, include=[]).get("ids", []))
        new_count = sum(1 for i in ids if i not in already)
        # 自定义 embed_fn 时手动算 embeddings，绕过 collection 的默认 embedding
        if self.embed_fn is not None:
            embs = self.embed_fn(docs)
            self.collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
        else:
            self.collection.upsert(ids=ids, documents=docs, metadatas=metas)
        return new_count

    def add_documents(self, docs: list[Document]) -> int:
        return sum(self.add_document(d) for d in docs)

    # ── 删除 / 对账 ───────────────────────────────────
    def delete_by_source(self, source_path: str) -> int:
        """删掉某个源文件的全部 block。返回删除条数。

        源文件被删除时必须调用，否则向量库会一直召回已经不存在的内容。
        """
        self._ensure_handle()
        before = self.count()
        self.collection.delete(where={"source_path": source_path})
        return max(0, before - self.count())

    def reconcile_source(self, source_path: str, current_block_ids: list[str]) -> int:
        """对账：该源文件现在只应有 `current_block_ids`，其余删除。返回删除条数。

        这是"文档被编辑后不留僵尸向量"的关键。因为 `Block.content_hash` 含正文，
        改一个字就是**新 id**，光靠 upsert 删不掉旧的那条 —— 必须显式对账。

        典型用法：
            doc = load(path)
            store.add_document(doc)
            store.reconcile_source(str(path), [b.content_hash for b in doc.blocks])
        """
        self._ensure_handle()
        before = self.count()
        if not current_block_ids:
            self.collection.delete(where={"source_path": source_path})
        else:
            self.collection.delete(
                where={
                    "$and": [
                        {"source_path": source_path},
                        {"content_hash": {"$nin": list(current_block_ids)}},
                    ]
                }
            )
        return max(0, before - self.count())

    # ── 查询 ─────────────────────────────────────────
    def query(self, q: str, k: int = 5, where: dict | None = None) -> list[dict]:
        """语义检索 → 返回 list of {id, text, metadata, distance}。"""
        self._ensure_handle()  # 句柄可能 stale(外部脚本重建过 chroma_db)
        kw: dict[str, Any] = {"n_results": k}
        if where:
            kw["where"] = where
        if self.embed_fn is not None:
            kw["query_embeddings"] = self.embed_fn([q])
        else:
            kw["query_texts"] = [q]
        res = self.collection.query(**kw)
        out: list[dict] = []
        if not res.get("ids") or not res["ids"][0]:
            return out
        for _i, (ids, docs, metas, dists) in enumerate(
            zip(
                res["ids"][0],
                res["documents"][0],
                res["metadatas"][0],
                res["distances"][0],
                strict=False,
            )
        ):
            out.append(
                {
                    "id": ids,
                    "text": docs,
                    "metadata": metas,
                    "distance": dists,
                }
            )
        return out

    def filter_by_type(self, block_type: BlockType) -> list[dict]:
        """按 block_type 拉所有记录（用于人工核查 / 评测）。"""
        self._ensure_handle()  # 句柄可能 stale
        res = self.collection.get(where={"block_type": block_type.value})
        out: list[dict] = []
        for _i, (rid, doc, meta) in enumerate(
            zip(
                res["ids"],
                res["documents"],
                res["metadatas"],
                strict=False,
            )
        ):
            out.append({"id": rid, "text": doc, "metadata": meta})
        return out

    def count(self) -> int:
        self._ensure_handle()  # 句柄可能 stale
        return self.collection.count()

    def reset(self) -> None:
        """清空 collection（仅用于测试）。"""
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection.name,
            metadata={"hnsw:space": "cosine"},
        )


__all__ = ["P5ChromaStore"]
