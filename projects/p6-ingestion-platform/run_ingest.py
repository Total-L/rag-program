"""P6 — 端到端摄取流水线（"企业级提取数据的整个流程"的可运行答案）。

    连接器 list（只元数据）
        → manifest diff（算出真正要做的活）
        → fetch 正文（只对 new/changed）
        → 解析（复用 P5 loaders；md/txt 直接解码）
        → 清洗归一化 + 质量过滤 + 近重复
        → PII 脱敏 + 敏感度自动分级
        → 分块 + 上下文前缀 + 元数据增强
        → 批量嵌入
        → 向量库 upsert + reconcile（删僵尸）
        → mark_indexed（下轮才能判 unchanged）
        → 源侧删除 → delete_by_source + forget

设计原则：
- **逐文档隔离**：任何一步抛异常只让这一篇进 DLQ，其余继续。
  （审计原文：一篇坏文档会炸掉整轮 reindex，失败对运维不可见。）
- **失败不 mark_indexed**：这样下一轮会自动重试，不需要额外的重试队列。
- **可恢复**：游标存在 manifest 里，中断后接着跑。
- 刻意**不引入 Airflow/Prefect**：编排属于"生产运维"阶段，本轮范围是数据三件套。
  这里只提供一个能被任意调度器调用的幂等入口。

跑：
    python run_ingest.py                      # 增量摄取（fs + confluence）
    python run_ingest.py --full-scan          # 全量扫描（允许判定删除）
    python run_ingest.py --embedder hash      # 无 Ollama 时验证流程
    python run_ingest.py --stats              # 只看当前状态
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from src.chunkers import layout_chunks, table_aware_chunks, to_chunks  # noqa: E402
from src.clean import (  # noqa: E402
    NearDupIndex,
    detect_language,
    find_repeated_lines,
    normalize_text,
    quality_check,
    strip_boilerplate,
)
from src.connectors import ConfluenceConnector, Connector, FileSystemConnector  # noqa: E402
from src.embeddings import get_embedder  # noqa: E402
from src.manifest import ManifestStore  # noqa: E402
from src.models import Chunk, IngestFailure, SourceRecord  # noqa: E402
from src.pii import classify_sensitivity, redact_for_index  # noqa: E402
from src.store import IndexStore, VectorRecord, open_index_store  # noqa: E402

log = logging.getLogger("p6.ingest")

REPO = HERE.parent.parent


@dataclass
class IngestStats:
    """一轮摄取的可观测结果。运维看这个判断有没有出问题。"""

    run_id: str = ""
    listed: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    docs_indexed: int = 0
    chunks_written: int = 0
    stale_removed: int = 0
    skipped_quality: int = 0
    skipped_neardup: int = 0
    pii_redacted_docs: int = 0
    sensitivity_upgraded: int = 0
    failures: list[IngestFailure] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "failures"}
        d["failures"] = len(self.failures)
        return d

    def report(self) -> str:
        lines = [
            f"run_id            {self.run_id}",
            f"列举               {self.listed}",
            f"  new/changed      {self.new}/{self.changed}  (需要重嵌)",
            f"  unchanged        {self.unchanged}  (跳过，0 次重嵌)",
            f"  deleted          {self.deleted}",
            f"入库文档           {self.docs_indexed}",
            f"写入 chunk         {self.chunks_written}",
            f"清除僵尸 chunk     {self.stale_removed}",
            f"质量过滤丢弃       {self.skipped_quality}",
            f"近重复丢弃         {self.skipped_neardup}",
            f"含 PII 已脱敏文档  {self.pii_redacted_docs}",
            f"敏感度自动升级     {self.sensitivity_upgraded}",
            f"失败(进 DLQ)       {len(self.failures)}",
            f"耗时               {self.elapsed_s:.1f}s",
        ]
        return "\n".join("  " + ln for ln in lines)


class IngestPipeline:
    """把三件套串起来的流水线。"""

    def __init__(
        self,
        *,
        store: IndexStore,
        manifest: ManifestStore,
        embedder,
        dlq_dir: Path,
        max_chars: int = 600,
        neardup_threshold: float = 0.92,
        add_context: bool = True,
        tenant_id: str = "_unassigned",
    ):
        if tenant_id == "":
            raise ValueError(
                "IngestPipeline requires tenant_id (S2 multi-tenancy: refuse to ingest orphans)"
            )
        self.store = store
        self.manifest = manifest
        self.embedder = embedder
        self.dlq_dir = dlq_dir
        self.dlq_dir.mkdir(parents=True, exist_ok=True)
        self.max_chars = max_chars
        self.dedup = NearDupIndex(threshold=neardup_threshold)
        self.add_context = add_context
        self.tenant_id = tenant_id
        # 语料级样板行（页眉/页脚/模板段落）。空集表示还没学习过。
        self._repeated: set[str] = set()
        self._body_cache: dict[str, str] = {}

    # ── 主入口 ───────────────────────────────────────
    def run(
        self, connectors: list[Connector], *, run_id: str, full_scan: bool = False
    ) -> IngestStats:
        t0 = time.time()
        st = IngestStats(run_id=run_id)

        for conn in connectors:
            # SaaS 类连接器不允许据列举判删除（防误删）
            allow_delete = full_scan and conn.supports_full_scan()
            cursor = "" if full_scan else self.manifest.get_cursor(conn.name)
            try:
                listed = list(conn.list_records(since_cursor=cursor))
            except Exception as e:  # noqa: BLE001
                self._to_dlq(IngestFailure(f"{conn.name}:*", "list", repr(e)))
                st.failures.append(IngestFailure(f"{conn.name}:*", "list", repr(e)))
                continue

            st.listed += len(listed)
            delta = self.manifest.diff(
                listed, run_id=run_id, full_scan=allow_delete, connector=conn.name
            )
            st.new += len(delta.new)
            st.changed += len(delta.changed)
            st.unchanged += len(delta.unchanged)

            # ★ 先学语料级样板，再做近重复判定。
            # 为什么必须分两趟：企业文档高度模板化（"## 适用对象 / - 全员 /
            # ## 常见问题" 这些段落每篇都有）。不先去掉这些共同样板，
            # MinHash 会把"年假政策"和"发票申请"判成 0.52 相似 —— 主题完全不同却被当重复丢掉。
            # 实测：去样板后同一对文档相似度从 0.516 掉到 0.281。
            self._learn_boilerplate(conn, delta.to_fetch, st)

            # 只对 new/changed 干重活
            for rec in delta.to_fetch:
                try:
                    self._ingest_one(conn, rec, st, run_id=run_id)
                except Exception as e:  # noqa: BLE001
                    # ★ 逐文档隔离：一篇坏文档不能炸掉整轮
                    f = IngestFailure(rec.source_id, "pipeline", repr(e), rec.uri)
                    self._to_dlq(f)
                    st.failures.append(f)
                    log.warning("文档失败(已进 DLQ)：%s -> %s", rec.source_id, e)

            # 源侧删除 → 向量库也要删
            for sid in delta.deleted:
                try:
                    self.store.delete_by_source(sid)
                    self.manifest.forget(sid)
                    st.deleted += 1
                except Exception as e:  # noqa: BLE001
                    self._to_dlq(IngestFailure(sid, "delete", repr(e)))

            if not full_scan:
                nc = conn.next_cursor()
                if nc:
                    self.manifest.set_cursor(conn.name, nc)

        st.elapsed_s = time.time() - t0
        return st

    # ── 学习语料级样板（第一趟）─────────────────────
    def _learn_boilerplate(
        self, conn: Connector, recs: list[SourceRecord], st: IngestStats
    ) -> None:
        """先把本批正文都取回来，统计跨文档重复行，作为样板行集合。

        代价：正文要读两次（这里读一次并缓存，`_ingest_one` 复用缓存，不重复下载）。
        收益：近重复判定不再被模板样板带偏。
        样本太少（<5 篇）时不学 —— 频率统计不可靠，宁可不做。
        """
        if len(recs) < 5:
            return
        texts: list[str] = []
        for rec in recs:
            try:
                raw = conn.fetch_body(rec)
            except Exception:  # noqa: BLE001 - 失败留给 _ingest_one 记 DLQ
                continue
            body = normalize_text(self._parse(rec.with_body(raw)))
            self._body_cache[rec.source_id] = body
            texts.append(body)
        if texts:
            found = find_repeated_lines(texts, ratio=0.5)
            self._repeated |= found
            log.info("学到 %d 条语料级样板行", len(found))

    # ── 单文档全链路 ─────────────────────────────────
    def _ingest_one(
        self, conn: Connector, rec: SourceRecord, st: IngestStats, *, run_id: str
    ) -> None:
        # 正文优先用第一趟缓存的，避免重复下载
        text = self._body_cache.pop(rec.source_id, None)
        if text is None:
            raw = conn.fetch_body(rec)
            text = normalize_text(self._parse(rec.with_body(raw)))

        # ② 优化：去样板（含语料级）→ 质量 → 近重复
        text, _ = strip_boilerplate(text, repeated_lines=self._repeated)
        verdict = quality_check(text)
        if not verdict.ok:
            st.skipped_quality += 1
            log.info("质量过滤丢弃 %s（%s）", rec.source_id, verdict.reason)
            self.manifest.mark_indexed(rec, run_id=run_id, chunk_count=0, tenant_id=self.tenant_id)
            return

        dups = self.dedup.add(rec.source_id, text)
        if dups:
            st.skipped_neardup += 1
            log.info("近重复丢弃 %s（≈ %s, sim=%.2f）", rec.source_id, dups[0][0], dups[0][1])
            self.manifest.mark_indexed(rec, run_id=run_id, chunk_count=0, tenant_id=self.tenant_id)
            return

        # 敏感度分级必须在**脱敏之前**做：
        # 一旦脱敏，身份证/密钥已被遮蔽，classify_sensitivity 就检不出来了，
        # 文档会被错判成 public —— 这是顺序敏感的一步，别调换。
        final_sens, reasons = classify_sensitivity(text, rec.sensitivity)
        if final_sens != rec.sensitivity:
            st.sensitivity_upgraded += 1
            log.info(
                "敏感度升级 %s: %s → %s（%s）", rec.source_id, rec.sensitivity, final_sens, reasons
            )

        # 再脱敏（写库前）
        redaction = redact_for_index(text)
        if not redaction.clean:
            st.pii_redacted_docs += 1
        text = redaction.text

        lang = detect_language(text)

        # 分块（表格感知优先，退回标题感知）
        sections = (
            table_aware_chunks(text)
            if "|" in text
            else layout_chunks(text, max_chars=self.max_chars)
        )
        if not sections:
            sections = layout_chunks(text, max_chars=self.max_chars)
        doc_title = rec.extra.get("rel_path") or rec.uri.rsplit("/", 1)[-1]
        chunks: list[Chunk] = to_chunks(
            sections, rec.source_id, doc_title=doc_title, add_context=self.add_context
        )
        chunks = [c for c in chunks if quality_check(c.text, min_chars=15).ok]
        if not chunks:
            st.skipped_quality += 1
            self.manifest.mark_indexed(rec, run_id=run_id, chunk_count=0, tenant_id=self.tenant_id)
            return

        # ③ 写库：批量嵌入 → upsert → reconcile
        prov = rec.provenance()
        prov["sensitivity"] = final_sens
        prov["language"] = lang
        prov["tenant_id"] = self.tenant_id  # S2 多租户：chunk metadata 落地 tenant_id
        vectors = self.embedder.embed([c.text for c in chunks])
        records = [
            VectorRecord(
                chunk_id=c.chunk_id,
                source_id=rec.source_id,
                text=c.text,
                embedding=v,
                metadata=c.to_metadata(prov),
            )
            for c, v in zip(chunks, vectors, strict=False)
        ]
        result = self.store.upsert(records)
        # ★ 对账：这篇文档现在只应有这些 chunk，其余（编辑前的旧块）删掉
        removed = self.store.reconcile_source(rec.source_id, [c.chunk_id for c in chunks])

        st.docs_indexed += 1
        st.chunks_written += result.written
        st.stale_removed += removed
        # 只有全链路成功才记 manifest → 失败的下轮自动重试
        self.manifest.mark_indexed(
            rec, run_id=run_id, chunk_count=len(chunks), tenant_id=self.tenant_id
        )

    def _parse(self, rec: SourceRecord) -> str:
        """解析正文。文本类直接解码；二进制格式复用 P5 loaders。"""
        assert rec.raw is not None
        if rec.source_format in ("md", "txt"):
            text = rec.raw.decode("utf-8", errors="replace")
            from src.connectors import frontmatter_provenance

            _, body = frontmatter_provenance(text)
            return body
        if rec.source_format == "html":
            return _strip_html(rec.raw.decode("utf-8", errors="replace"))
        # pdf/docx/xlsx → 复用 P5（需要临时落盘，P5 loaders 吃路径）
        return self._parse_with_p5(rec)

    def _parse_with_p5(self, rec: SourceRecord) -> str:
        import tempfile

        p5_src = REPO / "projects" / "p5-multi-format-loader"
        if str(p5_src) not in sys.path:
            sys.path.insert(0, str(p5_src))
        try:
            from src.loaders import load as p5_load  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"P5 loaders 不可用，无法解析 {rec.source_format}: {e}") from e
        assert rec.raw is not None
        with tempfile.NamedTemporaryFile(suffix=f".{rec.source_format}", delete=False) as fh:
            fh.write(rec.raw)
            tmp = Path(fh.name)
        try:
            doc = p5_load(tmp)
            return "\n\n".join(b.text or b.ocr_text for b in doc.blocks if (b.text or b.ocr_text))
        finally:
            tmp.unlink(missing_ok=True)

    def _to_dlq(self, failure: IngestFailure) -> None:
        """失败写 DLQ（JSONL 追加）。运维靠它知道"什么坏了"。"""
        path = self.dlq_dir / "failed.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(failure.to_json_dict(), ensure_ascii=False) + "\n")


def _strip_html(html: str) -> str:
    """极简 HTML → 文本（表格转 markdown 以便 table_aware_chunks 识别）。"""
    import re

    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    # 表格转 markdown 管道行
    html = re.sub(r"(?i)</t[hd]>\s*<t[hd][^>]*>", " | ", html)
    html = re.sub(r"(?i)<tr[^>]*>", "\n| ", html)
    html = re.sub(r"(?i)</tr>", " |", html)
    html = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n" + "#" * int(m.group(1)) + " ", html)
    html = re.sub(r"(?i)</(h[1-6]|p|div|li|br)>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    for a, b in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')]:
        text = text.replace(a, b)
    return text


# ══════════════════════════════════════════════════════
def build_default_connectors() -> list[Connector]:
    """默认数据源：本仓库既有的企业 fixture + Confluence 回放。"""
    conns: list[Connector] = []
    ent = REPO / "datasets" / "fixtures" / "enterprise"
    if ent.exists():
        conns.append(FileSystemConnector(ent, patterns=("*.md",)))
    cf = HERE / "fixtures" / "confluence_pages.json"
    if cf.exists():
        conns.append(ConfluenceConnector(fixture_path=cf))
    return conns


def main() -> int:
    ap = argparse.ArgumentParser(description="P6 企业级摄取流水线")
    ap.add_argument("--full-scan", action="store_true", help="全量扫描（允许判定删除）")
    ap.add_argument("--embedder", default="auto", choices=["auto", "ollama", "hash"])
    ap.add_argument("--backend", default="auto", choices=["auto", "pgvector", "chroma"])
    ap.add_argument("--data-dir", default=str(HERE / "data"))
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（快速验证）")
    ap.add_argument("--no-context", action="store_true", help="关闭上下文前缀")
    ap.add_argument("--stats", action="store_true", help="只打印当前状态后退出")
    ap.add_argument(
        "--tenant",
        default=os.environ.get("RAG_PROG_TENANT", ""),
        help="S2 多租户：必须指定 tenant_id（slug 格式）；缺则拒绝写入",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # S2 多租户：tenant 必填（防止无主数据落库；refuse to start 比悄悄跑更难排查）
    if not args.tenant:
        ap.error(
            "--tenant is required (S2 multi-tenancy: refuse to ingest without owner)\n"
            "  format: ^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$"
        )

    data_dir = Path(args.data_dir)
    manifest = ManifestStore(data_dir / "manifest.db")

    if args.stats:
        print("manifest:", manifest.stats())
        st = open_index_store(args.backend, chroma_path=data_dir / "index")
        print(f"index({st.backend_name}) 行数:", st.count())
        manifest.close()
        return 0

    embedder = get_embedder(args.embedder)
    dim = getattr(embedder, "dim", 768)
    store = open_index_store(
        args.backend,
        chroma_path=data_dir / "index",
        collection_name="p6_chunks",
        dim=dim,
    )
    print(f"→ 嵌入后端 {type(embedder).__name__} (dim={dim}) | 向量库 {store.backend_name}")

    conns = build_default_connectors()
    if not conns:
        print("✖ 没有可用数据源")
        return 1
    print(f"→ 数据源: {[c.name for c in conns]}")

    if args.limit:
        # 只是为了快速验证：截断每个连接器的列举结果
        for c in conns:
            orig = c.list_records

            def limited(since_cursor="", _orig=orig, _n=args.limit):
                for i, r in enumerate(_orig(since_cursor=since_cursor)):
                    if i >= _n:
                        return
                    yield r

            c.list_records = limited  # type: ignore[method-assign]

    pipe = IngestPipeline(
        store=store,
        manifest=manifest,
        embedder=embedder,
        dlq_dir=data_dir / "dlq",
        add_context=not args.no_context,
        tenant_id=args.tenant,
    )
    run_id = f"run-{int(time.time())}"
    print(f"\n=== 摄取开始 {run_id}（{'全量' if args.full_scan else '增量'}） tenant={args.tenant}")
    st = pipe.run(conns, run_id=run_id, full_scan=args.full_scan)
    print(st.report())
    print(f"\n  向量库总行数: {store.count()}")
    print(f"  嵌入统计: {getattr(embedder, 'stats', {})}")
    print(f"  租户: {args.tenant}")

    # 落一份运行报告，便于回归比对
    rep = data_dir / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / f"{run_id}.json").write_text(
        json.dumps(st.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if st.failures:
        print(f"\n⚠️  {len(st.failures)} 篇进入 DLQ: {data_dir / 'dlq' / 'failed.jsonl'}")
    manifest.close()
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
