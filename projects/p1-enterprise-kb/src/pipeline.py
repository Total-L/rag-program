"""P1 端到端 RAG Pipeline。

P1 自实现（详见 `_compat.py` + ADR-0005）：
- chunking：自实现 MarkdownHeadingSplitter
- embeddings：自实现 ollama_embed_passages / ollama_embed_query（HTTP 客户端）
- retrieval.context.pack_evidence：自实现 token budget 装箱
- retrieval.models.Candidate：自实现 dataclass
- security.is_allowed：自实现 path ACL

P1 特有：
- ACL 过滤（acl.filter_docs）
- 引用校验（必须指向真实 chunk_id）
- 拒答通道（must_abstain / 置信度阈值）
- 持久化 + 增量索引（P1IndexStore，详见 ADR-0004）
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 让 src 可被 import
sys.path.insert(0, str(Path(__file__).parent))

from acl import Sensitivity, User  # noqa: E402
from config import get_p1_config  # noqa: E402
from index_store import P1IndexStore, compute_chunk_id  # noqa: E402
from quota_store import get_quota_store  # noqa: E402
from tenant import (
    QuotaExceeded,  # noqa: E402
    validate_tenant_id,  # noqa: E402
)
from tenant_context import (  # noqa: E402
    reset_current_tenant,
    set_current_tenant,
)
from tenant_registry import get_registry  # noqa: E402


@dataclass
class P1Result:
    question: str
    answer: str
    citations: list[dict]
    abstained: bool
    user_role: str
    latency_ms: float
    stages: dict = field(default_factory=dict)
    # RCA 根因 4 修复：暴露 retrieved_source_ids 让 eval 能做真 hit/recall/MRR
    # (详见 RCA_p1_eval_regression.md)。来源是 _ask_legacy 检索阶段收集的 topk source_id。
    retrieved_source_ids: list[str] = field(default_factory=list)
    # 根因 4 收尾：暴露 retrieved_chunk_ids，平行于 retrieved_source_ids。
    # 用例：调试时定位"为什么 source_ids 命中但答案不对"——直接拿 chunk_id 看具体段落。
    retrieved_chunk_ids: list[str] = field(default_factory=list)


def _load_corpus(corpus_dir: Path) -> list[dict]:
    """加载企业文档（带 sensitivity 元数据）。"""
    docs = []
    for f in sorted(corpus_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        # 解析 frontmatter
        sensitivity = "public"
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                fm = text[4:end]
                for line in fm.splitlines():
                    if line.startswith("sensitivity:"):
                        sensitivity = line.split(":", 1)[1].strip()
                text = text[end + 5 :]
        docs.append(
            {
                "doc_id": f.stem,
                "text": text,
                "sensitivity": sensitivity,
            }
        )
    return docs


def _chunk_docs(docs: list[dict]) -> list[dict]:
    """对每篇文档做 chunking；用稳定 chunk_id（sha256）便于增量 upsert。

    用 `_compat.MarkdownHeadingSplitter`（P1 自实现，详见 ADR-0005）按标题路径切。
    不带 page_or_sheet —— 所以稳定 chunk_id 是
    `sha256(doc_id | heading_path | text)`（见 `index_store.compute_chunk_id`）。
    """
    from _compat import MarkdownHeadingSplitter as _Splitter

    splitter = _Splitter(max_chunk_size=400)

    out: list[dict] = []
    for d in docs:
        for c in splitter.split(d["text"]):
            # _compat.MarkdownHeadingSplitter.split() 返回 dict
            # 字段：{"text": str, "heading_path": str}
            text = c["text"] if isinstance(c, dict) else getattr(c, "text", "")
            if not text.strip():
                continue
            heading_path = (
                c["heading_path"] if isinstance(c, dict) else getattr(c, "heading_path", "")
            )
            out.append(
                {
                    "chunk_id": compute_chunk_id(d["doc_id"], heading_path, text),
                    "source_id": d["doc_id"],
                    "doc_id": d["doc_id"],
                    "text": text,
                    "sensitivity": d["sensitivity"],
                    "heading_path": heading_path,
                }
            )
    return out


def _cosine_topk(query_vec, doc_vecs, k: int) -> list[tuple[int, float]]:
    import numpy as np

    q = np.array(query_vec, dtype=np.float32)
    d = np.array(doc_vecs, dtype=np.float32)
    qn = q / max(float(np.linalg.norm(q)), 1e-9)
    dn = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
    sims = (dn @ qn).tolist()
    idx = sorted(range(len(sims)), key=lambda i: -sims[i])[:k]
    return [(int(i), float(sims[i])) for i in idx]


def _mmx_or_ollama(prompt: str, system: str = "") -> str:
    """调 mmx（M3）或 Ollama。"""
    if os.environ.get("RAG_PROG_LLM_BACKEND", "mmx") == "mmx":
        import subprocess

        try:
            r = subprocess.run(
                [
                    "mmx",
                    "text",
                    "chat",
                    "--model",
                    "MiniMax-M3",
                    "--max-tokens",
                    "512",
                    "--temperature",
                    "0.0",
                    "--output",
                    "json",
                    "--system",
                    system,
                    "--message",
                    prompt,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if r.returncode == 0:
                return json.loads(r.stdout).get("text", r.stdout)
            return f"（mmx failed: rc={r.returncode}）"
        except Exception as e:
            return f"（mmx exception: {e}）"
    # ollama fallback
    import urllib.request

    try:
        body = json.dumps(
            {
                "model": os.environ.get("OLLAMA_LLM_MODEL", "llama3.2:1b"),
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 512},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())["response"]
    except Exception as e:
        return f"（ollama failed: {e}）"


SYSTEM = """你是公司知识库助手。基于提供的证据回答。
规则：
1. 答案必须基于证据
2. 每条事实用 [编号] 引用证据
3. 只有当证据为空、或证据与问题完全无关时，才回答"我无法在知识库中找到相关信息"并将 abstained 设为 true；只要证据含部分相关事实，就基于证据给答案
4. 禁止编造：不要把没在证据里出现的数字/姓名/日期写进答案
5. 用 JSON 输出：{"answer":"...","citations":["E1","E2"],"abstained":false}，abstained=true 仅在 answer 完全是拒答短语时
"""


# LLM 拒答短语表（仅在 JSON 解析失败时启用，作为 heuristic fallback）
_ABSTAIN_PHRASES = [
    "我无法在知识库中找到",
    "无法在知识库中找到",
    "I cannot find",
    "no information",
]


def _parse_llm_response(raw: str) -> tuple[str, list, bool]:
    """解析 LLM 输出 → (answer, citations, abstained)。

    契约（根因 3 修复后）：
    1) JSON 解析成功 → 尊重 LLM 的 abstained 字段，heuristic 不覆盖
    2) JSON 解析失败 → 启发式判断：仅当 answer 前 30 字含完整拒答短语时标 abstained
    3) markers 收紧为完整短语（"找不到"/"知识库中没" 不再触发，因为常见词）

    返回的 citations 是 LLM 自报的引用 ID（未校验），调用方需做引用校验。
    """
    abstained = False
    answer = raw
    citations: list = []
    json_parsed = False
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            resp = json.loads(raw[start : end + 1])
            answer = resp.get("answer", raw)
            citations = resp.get("citations", [])
            abstained = resp.get("abstained", False)
            json_parsed = True
    except Exception:
        pass

    if not json_parsed and not abstained:
        head = (answer or "").strip()[:30]
        if any(p in head for p in _ABSTAIN_PHRASES):
            abstained = True

    return answer, citations, abstained


class P1Pipeline:
    def __init__(self, user: User):
        self.cfg = get_p1_config()
        self.user = user
        self.docs: list[dict] = []
        # ★ 持久化向量库：替代原来的内存 `self.index: list[dict]`
        # self.index 仍是 list[dict] 形态（consumer 代码不动），
        # 但实际由 P1IndexStore 持久化，查询时按 ACL 懒加载。
        # 详见 ADR-0004。
        self._indexed: bool = False
        self._index_cache: list[dict] | None = None
        # ★ BM25 缓存（根因 5 修复）：避免每次 query 重建 BM25Okapi
        # cache key = (user_role, sorted_chunk_ids tuple)；用户角色/索引变化时自动失效
        # 详见 RCA_p1_eval_regression.md 根因 5。
        self._bm25_cache_key: tuple | None = None
        self._bm25 = None  # BM25Okapi 实例
        self._bm25_corpus_tokens: list[list[str]] | None = None
        self.store = P1IndexStore(
            chroma_path=self.cfg.eval_dir.parent / "index",
            dim=768,
        )
        self._embed_query = None  # ADR-0005 后已移除 BGE 备选支线，统一走 Ollama
        # 统一走 Ollama（ADR-0005：删除原 kbchat-bge 备选支线，避免与训练营耦合）
        self._embed_fn = self._ollama_embed_passages

    @property
    def index(self) -> list[dict]:
        """查询时按用户 ACL + tenant 懒加载；空索引（未跑过 index_corpus）返回 []。

        ACL 过滤**从 build 期挪到 query 期**是 ADR-0004 的核心改动：
        - 旧：每个用户一份内存索引（成本×N、角色变更需重建）
        - 新：共享持久索引 + 查询时 `where={"sensitivity": {...allowed...}}`
        P6 IndexStore 的 `fetch_all` 严格按 metadata 过滤，过滤器绕过 = 数据不出库。

        S2 多租户：再加 `where={"tenant_id": ...}` 强制隔离。tenant 必须已在
        context 里 —— 否则 raise TenantRequiredError（防止任何路径漏注入）。
        """
        if not self._indexed:
            return []
        if self._index_cache is None:
            # ★ S2：tenant 必须在 context 里才能查（隔离的核心守卫）
            from tenant_context import require_current_tenant

            tenant = require_current_tenant()
            allowed = [s.name.lower() for s in Sensitivity if self.user.can_see(s.name.lower())]
            self._index_cache = self.store.fetch_for_user(
                allowed_sensitivities=allowed,
                tenant_id=tenant.tenant_id,
            )
        return self._index_cache

    def _ollama_embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Ollama embedding 客户端。委派给 `_compat.ollama_embed_passages`（详见 ADR-0005）。"""
        from _compat import ollama_embed_passages

        return ollama_embed_passages(texts)

    def _get_bm25(self) -> tuple:
        """获取缓存的 BM25Okapi 实例与 corpus tokens（根因 5 优化）。

        cache key = (user_role, sorted(chunk_ids))，保证：
        - 用户角色变化 → 失效（不同 ACL 视图的 corpus 不同）
        - 索引 chunk 增删改 → 失效（chunk_id 集合变化）
        - 索引未变 → 复用（省去 ~100-500ms 的 tokenize + BM25Okapi 构建）

        Returns:
            (BM25Okapi, list[list[str]]) tuple

        详见 RCA_p1_eval_regression.md 根因 5。
        """
        import re

        from rank_bm25 import BM25Okapi

        def _tok(t):
            return re.findall(r"[A-Za-z]+|\d+|[一-鿿]", t)

        idx = self.index
        chunk_ids_sorted = sorted(c["chunk_id"] for c in idx)
        cache_key = (self.user.role, tuple(chunk_ids_sorted))
        if self._bm25_cache_key == cache_key and self._bm25 is not None:
            return self._bm25, self._bm25_corpus_tokens
        # 重建
        corpus_tokens = [_tok(c["text"]) for c in idx]
        bm = BM25Okapi(corpus_tokens)
        self._bm25 = bm
        self._bm25_corpus_tokens = corpus_tokens
        self._bm25_cache_key = cache_key
        return bm, corpus_tokens

    def index_corpus(self, *, tenant_id: str | None = None) -> None:
        """**增量 + 持久化** 摄取：第二次跑同一语料 ≈ 0 重嵌。

        流程：
            1) load all docs（**不做** ACL 过滤 —— ACL 挪到 query 期，详见 ADR-0004）
            2) chunk with stable chunk_id
            3) `store.build_index(chunks, embed_fn)` —— 只对 new/changed 重嵌
            4) invalidate lazy cache

        注意：旧实现 `self.index = _embed_corpus(...)` 把整个索引装进内存；
        现在 `self.index` 是 property（按用户 ACL 懒加载），写库是
        `self.store.upsert(...)`。这是 **P1 旗舰从 in-memory → 持久化的核心改动**。

        S2 多租户：
        - `tenant_id` 必填（每个 P1Pipeline 实例绑一个 tenant）；写库时所有
          chunk metadata 强制注入 tenant_id，fetch_for_user where 才能命中。
        - 缺则从 current context 读（生产 ingest 通常已在 run_ingest.py 注入），
          都没有则 fallback 到 `_unassigned`（dev 兼容；生产应报错）。
        """
        if not self.cfg.corpus_dir.exists():
            self.cfg.corpus_dir.mkdir(parents=True, exist_ok=True)
            return
        self.docs = _load_corpus(self.cfg.corpus_dir)
        # ★ ACL 不再在这里过滤：所有文档落库，靠 query 期的 `fetch_for_user`
        # 按 `sensitivity` 过滤。这样：
        #   (a) 切换用户角色不需要 reindex
        #   (b) 角色升级即时生效（不用重建索引）
        #   (c) 多份重复向量的成本消除（pgvector 直接 WHERE 推库）
        chunks = _chunk_docs(self.docs)
        # S2：注入 tenant_id 到每条 chunk，vector store metadata 落地
        effective_tid = tenant_id or "_unassigned"
        for c in chunks:
            c["tenant_id"] = effective_tid
        stats = self.store.build_index(chunks, self._embed_fn)
        self._indexed = True
        self._index_cache = None  # 强制下次读 self.index 重新按 ACL 加载
        # ★ 索引变了 → BM25 缓存必失效（虽然 cache key 也会失效，但显式清更明确）
        self._bm25_cache_key = None
        self._bm25 = None
        self._bm25_corpus_tokens = None
        print(f"→ {stats.report()}  backend={self.store.backend_name}")

    def ask(self, question: str, *, tenant_id: str, trace_id: str | None = None) -> P1Result:
        """主入口: 走 LangGraph(ask_agentic)。

        Args:
            question: 用户问题
            tenant_id: 必填。隔离 + 配额的核心键；缺则 raise TenantIdError。
            trace_id: 可选 trace ID（不传则自动生成 16 位 hex）。
                     用于关联日志 + metrics（M1 可观测基线）。
        """
        return self.ask_agentic(question, tenant_id=tenant_id, max_retries=2, trace_id=trace_id)

    def ask_stream(
        self,
        question: str,
        *,
        tenant_id: str,
        trace_id: str | None = None,
    ):
        """generator: 阶段事件流（D7-T1；与 SSE 端点 D7-T2 配套）。

        事件序列（normal path）：
            {"event": "started", "trace_id": tid, "tenant_id": tid, "ts": iso}
            {"event": "retrieve_started", "ts": iso}
            {"event": "retrieve_done", "indexed_chunks": N}
            {"event": "generate_started"}
            {"event": "done", "result": {...P1Result as dict...}, "latency_ms": ...}

        失败路径：
            {"event": "error", "status": "quota_exceeded|internal_error", "message": ...}

        不真做到 token 级流：当前 mmx / Ollama 路径都等 LLM 全量返回；
        接 streaming LLM（ollama ?stream=true）后可在此加 token 事件。

        Yields:
            dict 事件（server.py 转 SSE data: {...json...}\\n\\n 格式）
        """
        from dataclasses import asdict as _asdict
        from datetime import UTC
        from datetime import datetime as _dt

        from observability import new_trace_id, record_prompt_guard
        from prompt_guard import (
            BlockedByGuardError,
            InputTooLongError,
            sanitize_query,
        )

        # ── S4-T1：query 入参消毒（ask_stream 入口）──
        # 入参先 sanitise（长度 + 注入模式 fail-closed）；
        # 命中 → 抛 GuardError（外层 SSE 转 error 事件；不暴露具体 reason 给客户端）。
        try:
            tid = trace_id or new_trace_id()
            safe_question = sanitize_query(question)
        except (InputTooLongError, BlockedByGuardError) as e:
            record_prompt_guard(action="block", kind=type(e).__name__, tenant_id=tenant_id)
            yield {
                "event": "error",
                "status": "input_guarded",
                "reason": e.reason,
                "trace_id": tid if 'tid' in locals() else new_trace_id(),
            }
            return
        except Exception as e:
            # 其他 sanitize 异常（empty 等）→ 也走 error
            record_prompt_guard(action="block", kind="guard_error", tenant_id=tenant_id)
            yield {
                "event": "error",
                "status": "input_guarded",
                "reason": "GUARD_ERROR",
                "message": str(e)[:120],
                "trace_id": tid if 'tid' in locals() else new_trace_id(),
            }
            return
        ts0 = _dt.now(UTC).isoformat(timespec="seconds")
        yield {
            "event": "started",
            "trace_id": tid,
            "tenant_id": tenant_id,
            "ts": ts0,
        }
        yield {
            "event": "retrieve_started",
            "ts": _dt.now(UTC).isoformat(timespec="seconds"),
        }
        # 实际 retrieve+generate 都由 ask_agentic 内部完成；此处只发事件不展开
        yield {
            "event": "retrieve_done",
            "ts": _dt.now(UTC).isoformat(timespec="seconds"),
        }
        yield {
            "event": "generate_started",
            "ts": _dt.now(UTC).isoformat(timespec="seconds"),
        }
        try:
            result = self.ask(question, tenant_id=tenant_id, trace_id=tid)
        except QuotaExceeded as e:
            yield {
                "event": "error",
                "status": "quota_exceeded",
                "message": str(e),
                "trace_id": tid,
            }
            return
        except Exception as e:
            yield {
                "event": "error",
                "status": "internal_error",
                "message": str(e)[:200],
                "trace_id": tid,
            }
            return
        yield {
            "event": "done",
            "result": _asdict(result),
            "latency_ms": result.latency_ms,
            "trace_id": tid,
        }

    def ask_agentic(
        self,
        question: str,
        *,
        tenant_id: str,
        max_retries: int = 2,
        trace_id: str | None = None,
    ) -> P1Result:
        """Agentic RAG 入口:LangGraph 编排的 Self-RAG 反思 + CRAG 改写循环。

        选 best attempt 桥接成 P1Result 返回,字段与 ask() 等价。
        空索引 fallback 到「无索引数据」拒答,与 ask() 一致。

        S2 多租户：
        - tenant_id **必填**，格式校验 + 注册表查找 + 配额预扣 + context 注入
        - 失败路径 refund（已扣的 queries 配额退掉）
        - 内部 `self.index` 自动从 context 读 tenant，强制按 (tenant_id, sensitivity) 过滤

        Args:
            question: 用户问题
            tenant_id: 必填。租户 ID（slug 格式）；缺/无效/未注册 → raise。
            max_retries: CRAG 改写最大次数
            trace_id: 可选 trace ID（M1 可观测基线，详见 observability.py）
        """
        # ── S2：多租户隔离与配额 ──
        # 校验 → 查注册表 → 配额预扣 → set context
        # 任一步失败则透传异常（问询方拿到的就是原始错误信息，便于 4xx 决策）
        tid = validate_tenant_id(tenant_id)
        tenant = get_registry().get(tid)
        quota = get_quota_store()

        # ── M1：埋点 trace_id + structlog 日志 + metrics ──
        from observability import (
            QueryStatus,
            bind_trace_id,
            configure_logging,
            get_logger,
            new_trace_id,
            record_audit_write,
            record_query,
            unbind_trace_id,
        )

        # 确保 structlog 配置过一次（幂等）
        try:
            configure_logging(level="INFO")
        except Exception:
            pass
        log = get_logger("rag.pipeline")

        tid = trace_id or new_trace_id()
        token = bind_trace_id(tid)
        import time

        t0 = time.time()
        # D6-T2：status 用 QueryStatus 枚举值（字符串），让 Prometheus label / audit
        # status / structlog 三处一致；后续加新状态只追加枚举值即可。
        status: str = QueryStatus.SUCCESS.value
        # D6-T2：把 quota.check_and_consume 移进 try 块——这样 QuotaExceeded 路径
        # 也能走 finally 上报 metrics（status=quota_exceeded），运维能基于此设告警。
        # 反之 status=internal_error 时触发 refund（见 finally 块）。
        queries_refunded = False
        tenant_token = None  # type: ignore[assignment]
        # S3：audit 写入需要 P1Result，所以在 try 块里追踪；
        # exception path 无 result → audit 跳过（metrics 已记 status=error）。
        result: P1Result | None = None
        try:
            quota.check_and_consume(tenant, queries=1)  # QuotaExceeded → finally + raise
            tenant_token = set_current_tenant(tenant)
            if not self.index:
                status = QueryStatus.NOT_READY.value
                log.warning("query_skipped_no_index", question=question[:80])
                result = P1Result(
                    question=question,
                    answer="（无索引数据，请先跑 datasets/generators/enterprise.py）",
                    citations=[],
                    abstained=True,
                    user_role=self.user.role,
                    latency_ms=0.0,
                    retrieved_source_ids=[],
                    stages={"trace_id": tid},  # M1：trace_id 一致贯穿所有早返路径
                )
                return result
            # 延迟导入避免循环(agentic_graph 内部 from pipeline import P1Pipeline/P1Result)
            from agentic_graph import ask_agentic as _ask_graph

            out = _ask_graph(self, question, max_retries=max_retries)
            # 选 best attempt: 优先 abstained=False 且 citations 最多;都没有 fallback 最后一次
            best = None
            for a in out.get("attempts", []):
                if a.get("abstained"):
                    continue
                if best is None or len(a.get("citations", [])) > len(best.get("citations", [])):
                    best = a
            if best is None and out.get("attempts"):
                best = out["attempts"][-1]
            if best is None:
                status = QueryStatus.GRAPH_NO_ATTEMPT.value
                log.error("graph_no_attempt", question=question[:80])
                result = P1Result(
                    question=question,
                    answer="(graph 无 attempt 返回)",
                    citations=[],
                    abstained=True,
                    user_role=self.user.role,
                    latency_ms=0.0,
                    retrieved_source_ids=[],
                    stages={"trace_id": tid},
                )
                return result
            total_latency = sum(a["latency_ms"] for a in out["attempts"])
            log.info(
                "query_done",
                attempts=len(out["attempts"]),
                latency_ms=round(total_latency, 1),
                abstained=bool(best.get("abstained")),
                citations=len(best.get("citations", [])),
            )
            result = P1Result(
                question=question,
                answer=best["answer"],
                citations=best["citations"],
                abstained=best["abstained"],
                user_role=self.user.role,
                latency_ms=total_latency,
                stages={
                    "indexed_chunks": len(self.index),
                    "attempts": len(out["attempts"]),
                    "graph_log_steps": len(out.get("log", [])),
                    "trace_id": tid,  # M1：把 trace_id 也写进 stages
                },
                retrieved_source_ids=best.get("retrieved_source_ids", []),
                retrieved_chunk_ids=best.get("retrieved_chunk_ids", []),
            )
            return result
        except QuotaExceeded:
            # D6-T2：把配额超限从 catch-all error 拆出来，便于设单独告警 + 区分
            # "客户端行为（429）" vs "系统故障（500）"。
            status = QueryStatus.QUOTA_EXCEEDED.value
            log.warning(
                "query_quota_exceeded",
                tenant_id=tenant.tenant_id,
                user_id=self.user.user_id or "anonymous",
            )
            raise
        except Exception:
            status = QueryStatus.INTERNAL_ERROR.value
            log.exception("query_failed", question=question[:80])
            raise
        finally:
            # 上报 metrics（不吞原异常）
            latency_seconds = time.time() - t0
            try:
                record_query(
                    status=status, latency_seconds=latency_seconds, tenant_id=tenant.tenant_id
                )
            except Exception:
                pass
            # ── S3：审计日志（GDPR / 合规）──
            # 仅在 result 已构造时写（异常路径无 result；status=error 已有 metrics + log）
            # 写入失败：吞掉（query 不能因磁盘满挂掉），但上报 metric 让运维可见。
            if result is not None:
                try:
                    from audit import get_audit_logger

                    audit_log = get_audit_logger()
                    evt = audit_log.build_event(
                        tenant_id=tenant.tenant_id,
                        user_id=self.user.user_id or "anonymous",
                        question=result.question,
                        answer=result.answer,
                        trace_id=tid,
                        status=status,
                        citations=[c.get("id", "") for c in result.citations if isinstance(c, dict)]
                        + [c for c in result.citations if isinstance(c, str)],
                        retrieved_source_ids=result.retrieved_source_ids,
                        retrieved_chunk_ids=result.retrieved_chunk_ids,
                        abstained=result.abstained,
                        latency_ms=result.latency_ms,
                    )
                    audit_log.write(evt)
                    record_audit_write(status="success", tenant_id=tenant.tenant_id)
                except Exception as audit_err:
                    log.warning("audit_write_failed", error=str(audit_err)[:120])
                    try:
                        record_audit_write(status="failure", tenant_id=tenant.tenant_id)
                    except Exception:
                        pass
            unbind_trace_id(token)
            # ── S2：tenant context 清理 + 失败退款 ──
            # D6-T2：refund 仅在 pipeline 内部异常时（status=internal_error）。
            # quota_exceeded 时 check_and_consume raise 前未真正写入 events，无需 refund。
            if status == QueryStatus.INTERNAL_ERROR.value and not queries_refunded:
                try:
                    quota.refund(tenant, queries=1)
                    queries_refunded = True
                except Exception:
                    pass  # refund 失败不能影响主异常上抛
            if tenant_token is not None:
                try:
                    reset_current_tenant(tenant_token)
                except Exception:
                    pass
            # ── D7-T5：webhook 事件派发（fire-and-forget）──
            # 主路径 success/abstained 触发 query_completed；quota_exceeded 单独触发。
            # 内部 error 也发（订阅者可设专门告警）；吞所有异常不让 webhook 影响主流程。
            try:
                from webhooks import dispatch_async

                event_payload = {
                    "trace_id": tid,
                    "status": status,
                    "abstained": result.abstained if result is not None else False,
                    "latency_ms": result.latency_ms if result is not None else 0.0,
                    "user_id": self.user.user_id or "anonymous",
                }
                if status == QueryStatus.QUOTA_EXCEEDED.value:
                    dispatch_async(
                        tenant_id=tenant.tenant_id,
                        event="quota_exceeded",
                        payload=event_payload,
                    )
                elif status in (QueryStatus.SUCCESS.value, QueryStatus.ABSTAINED.value):
                    dispatch_async(
                        tenant_id=tenant.tenant_id,
                        event="query_completed",
                        payload=event_payload,
                    )
            except Exception as hook_err:
                log.warning("webhook_dispatch_failed", error=str(hook_err)[:120])

    def _ask_legacy(self, question: str) -> P1Result:
        """旧 ask() 实现保留为 _ask_legacy(),供对照实验或回滚用。

        当前 ask() 走 ask_agentic()(LangGraph 主路径)。
        如需还原单次线性 RAG,直接 return self._ask_legacy(question)。
        """
        if not self.index:
            return P1Result(
                question=question,
                answer="（无索引数据，请先跑 datasets/generators/enterprise.py）",
                citations=[],
                abstained=True,
                user_role=self.user.role,
                latency_ms=0.0,
            )
        import time

        t0 = time.time()

        # 1. embed query（统一走 Ollama，ADR-0005 后无 kbchat-bge 备选支线）
        qv = self._ollama_embed_passages([question])[0]

        # 2. dense top-k (取多些，再过滤掉纯标题/重复 chunk)
        doc_vecs = [c["embedding"] for c in self.index]
        dense_hits = _cosine_topk(qv, doc_vecs, k=self.cfg.top_k_dense * 8)

        # 过滤：丢掉纯标题/短问句 chunk（<25 字符或纯 markdown 标题/问号结尾）
        def _is_title_only(idx: int) -> bool:
            t = self.index[idx]["text"].strip()
            if len(t) < 25:
                return True
            if t.startswith("#") and "\n" not in t:
                return True
            if t.endswith("？") or t.endswith("?"):
                return True
            return False

        dense_hits = [(i, s) for i, s in dense_hits if not _is_title_only(i)]
        # 3. BM25 top-k (用 rank_bm25 直接算，绕过 kbchat BM25Index 的 Chunk 依赖)
        # 根因 5 优化：用 _get_bm25() 缓存实例，避免每次 query 重建
        bm, bm_corpus = self._get_bm25()
        import re

        def _tok(t):
            return re.findall(r"[A-Za-z]+|\d+|[一-鿿]", t)

        bm_scores = bm.get_scores(_tok(question))
        bm_pairs = sorted(enumerate(bm_scores), key=lambda p: -p[1])[: self.cfg.top_k_sparse * 4]
        # 4. RRF 融合（dense + sparse）— 用 (chunk_id, fused_score) 形式
        from collections import defaultdict

        rrf_scores: dict[str, float] = defaultdict(float)
        for r, (idx, _) in enumerate(dense_hits, 1):
            rrf_scores[self.index[idx]["chunk_id"]] += 1.0 / (self.cfg.rrf_k + r)
        # 给 BM25 更高权重（这个 corpus 上 dense 偏弱）
        bm_weight = float(os.environ.get("P1_BM25_WEIGHT", "2.0"))
        for r, (idx, _) in enumerate(bm_pairs, 1):
            rrf_scores[self.index[idx]["chunk_id"]] += bm_weight / (self.cfg.rrf_k + r)
        fused_sorted = sorted(rrf_scores.items(), key=lambda x: -x[1])
        topk_idx = []
        for cid, _ in fused_sorted[: self.cfg.top_k_final * 3]:
            for i, c in enumerate(self.index):
                if c["chunk_id"] == cid and not _is_title_only(i):
                    topk_idx.append(i)
                    break
            if len(topk_idx) >= self.cfg.top_k_final:
                break

        # 5. context pack（用 _compat 自实现的 pack_evidence + Candidate，见 ADR-0005）
        from _compat import Candidate, pack_evidence

        candidates = [
            Candidate(doc_id=self.index[i]["chunk_id"], text=self.index[i]["text"], score=1.0)
            for i in topk_idx
        ]
        packed = pack_evidence(candidates, token_budget=2400, per_source_cap=2)
        evidence = [
            {"id": f"E{i + 1}", "text": p.text, "chunk_id": p.doc_id} for i, p in enumerate(packed)
        ]

        # 5b. 收集 retrieved source_ids 与 chunk_ids（按 topk 顺序，保留重复）
        # source_ids 供 eval 做真 hit/MRR/recall（根因 4 修复）
        # chunk_ids 供调试定位具体段落（根因 4 收尾）
        retrieved_source_ids = [self.index[i]["source_id"] for i in topk_idx]
        retrieved_chunk_ids = [self.index[i]["chunk_id"] for i in topk_idx]

        # 6. grounded generation（S4-T3：证据隔离 + system prompt 升级）
        # - 证据段用 <documents>...</documents> 包裹 + 显式 system 指令
        # - ENHANCED_SYSTEM 让 LLM 明确视 evidence 为不可信数据
        from prompt_guard import (
            ENHANCED_SYSTEM,
            build_user_prompt,
            validate_output,
        )

        valid_ids = {e["id"] for e in evidence}
        # 入参已 sanitize（S4-T1）；失败这里不会到（早返）
        prompt = build_user_prompt(evidence, question)
        raw = _mmx_or_ollama(prompt, system=ENHANCED_SYSTEM)

        # 7. S4-T4：输出 schema 强校验（替换旧 _parse_llm_response）
        # validate_output 拿不到结构化结果 → 自动 abstain + 记 metric
        valid_out = validate_output(raw, valid_citation_ids=valid_ids)
        if valid_out.parsed is not None:
            answer = valid_out.parsed.answer
            citations = valid_out.parsed.citations
            abstained = valid_out.parsed.abstained
            output_validation_reason = valid_out.reason or "ok"
        else:
            # S4-T4 fallback：拿不到结构化结果仍走旧 _parse 抢救一下
            answer, citations, abstained = _parse_llm_response(raw)
            output_validation_reason = valid_out.reason  # e.g. "parse_error"/"schema_mismatch"/"injection_marker_in_output"

        # 8. 引用校验：必须指向真实证据
        valid_citations = [c for c in citations if c in valid_ids]
        if citations and not valid_citations:
            abstained = True
            answer = "我无法在知识库中找到相关信息。"

        return P1Result(
            question=question,
            answer=answer,
            citations=valid_citations,
            abstained=abstained,
            user_role=self.user.role,
            latency_ms=(time.time() - t0) * 1000,
            stages={
                "indexed_chunks": len(self.index),
                "retrieved": len(evidence),
                "guard_validation": output_validation_reason,  # S4-T4: 诊断
            },
            retrieved_source_ids=retrieved_source_ids,
            retrieved_chunk_ids=retrieved_chunk_ids,
        )

