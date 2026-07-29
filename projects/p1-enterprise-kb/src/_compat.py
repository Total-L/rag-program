"""P1 自实现兼容层：原 kbchat 接口的本地替身。

为什么不直接复用 rag-course/kbchat：
- P1 是企业程序本体的旗舰，必须 `pip install -e .` 就能跑。
- 与训练营共用 venv / 共用 editable install 会让 kbchat 上游 API 漂移无声影响企业程序。
- 这一层是 ~120 行的精简替身，覆盖 P1 实际用到的子集（markdown heading 分块、token budget
  装箱、embedding HTTP 客户端、path ACL）。每个函数都附 ADR-0005 说明为何自实现。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# ────────────────────────────────────────────────────────────────────
# 1. Markdown heading 分块
# ────────────────────────────────────────────────────────────────────


@dataclass
class _Section:
    """Markdown heading 路径下的一段文本。"""

    heading_path: list[str]
    body: str

    def to_chunk(self) -> dict:
        return {
            "heading_path": " / ".join(self.heading_path) if self.heading_path else "",
            "text": self.body.strip(),
        }


class MarkdownHeadingSplitter:
    """精简版 markdown heading 切分器。

    - 按 `#`/`##`/`###`/`####`/`#####`/`######` 行作为切分边界
    - 每段保留 `heading_path`（从根到当前标题的路径）
    - 单段超过 max_chunk_size 时，按 `\n\n` 再切窗
    - 与 kbchat.MarkdownHeadingSplitter API 兼容：实例化后调用 `split(text)`
      → 产出 {"text", "heading_path"} dict
    """

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

    def __init__(self, max_chunk_size: int = 400, overlap: int = 0) -> None:
        self.max_chunk_size = int(max_chunk_size)
        self.overlap = int(overlap)

    def split(self, text: str) -> Iterable[dict]:
        # 找所有 heading 行
        matches = list(self._HEADING_RE.finditer(text))
        if not matches:
            # 无标题：整段当一段
            for piece in self._window(text):
                yield {"heading_path": "", "text": piece}
            return

        # 起始虚拟 root（保证第一个 heading 也有父）
        path_stack: list[tuple[int, str]] = []
        cursor = 0

        for _i, m in enumerate(matches):
            # heading 之前的 body → 归到当前 path
            if cursor < m.start():
                body = text[cursor : m.start()]
                yield from self._yield_with_path(path_stack, body)
            # 更新 path stack
            level = len(m.group(1))
            title = m.group(2).strip()
            # 弹出同级或更深
            path_stack = [(lvl, t) for lvl, t in path_stack if lvl < level]
            path_stack.append((level, title))
            cursor = m.end()

        # 收尾
        if cursor < len(text):
            body = text[cursor:]
            self._yield_with_path(path_stack, body)

    # helpers --------------------------------------------------

    def _yield_with_path(self, stack: list[tuple[int, str]], body: str) -> Iterable[dict]:
        if not body.strip():
            return
        path = " / ".join(t for _, t in stack)
        for piece in self._window(body):
            yield {"heading_path": path, "text": piece.strip()}

    def _window(self, body: str) -> list[str]:
        if len(body) <= self.max_chunk_size:
            return [body]
        # 按段落 (\n\n) 切窗
        paragraphs = re.split(r"\n\n+", body)
        chunks: list[str] = []
        buf = ""
        for p in paragraphs:
            candidate = (buf + "\n\n" + p) if buf else p
            if len(candidate) <= self.max_chunk_size:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                # 单段就超长：硬切
                if len(p) > self.max_chunk_size:
                    for i in range(0, len(p), self.max_chunk_size):
                        chunks.append(p[i : i + self.max_chunk_size])
                    buf = ""
                else:
                    buf = p
        if buf:
            chunks.append(buf)
        return chunks


# ────────────────────────────────────────────────────────────────────
# 2. Candidate + context pack（token budget 装箱）
# ────────────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    """候选证据片段，用于 prompt 装箱。

    与原 kbchat.models.Candidate 兼容字段：doc_id/text/score。
    额外字段 source 用于 per_source_cap。
    """

    doc_id: str
    text: str
    score: float = 0.0
    source: str = ""  # 用于 per_source_cap


def _approx_token_count(text: str) -> int:
    """粗略 token 估算：中英混合按字符数 / 1.5 + 英文按空格分词数取 max。
    无 tiktoken 依赖，足够做 budget 装箱。
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
    ascii_words = len(re.findall(r"[A-Za-z]+", text))
    # 中文 1 字 ≈ 1 token；英文 1 词 ≈ 1.3 token
    return max(chinese_chars, int(ascii_words * 1.3))


def pack_evidence(
    candidates: Iterable[Candidate],
    *,
    token_budget: int = 2400,
    per_source_cap: int = 2,
) -> list[Candidate]:
    """贪心装箱：按 score 降序选入，受 token_budget 与 per_source_cap 约束。

    返回新的 list[Candidate]（不修改入参）。
    """
    by_source_count: dict[str, int] = {}
    used_tokens = 0
    out: list[Candidate] = []
    for c in sorted(candidates, key=lambda x: -x.score):
        if not c.source:
            src = c.doc_id.split("::", 1)[0] if "::" in c.doc_id else c.doc_id
            c = Candidate(doc_id=c.doc_id, text=c.text, score=c.score, source=src)
        if by_source_count.get(c.source, 0) >= per_source_cap:
            continue
        cost = _approx_token_count(c.text)
        if used_tokens + cost > token_budget and out:
            # 已选入至少一段就停；允许最后一段超 budget（避免零返回）
            continue
        out.append(c)
        used_tokens += cost
        by_source_count[c.source] = by_source_count.get(c.source, 0) + 1
    return out


# ────────────────────────────────────────────────────────────────────
# 3. Ollama embedding 客户端（共享给 P1Pipeline 内部）
# ────────────────────────────────────────────────────────────────────


def ollama_embed_passages(
    texts: list[str],
    *,
    host: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> list[list[float]]:
    """批量调 Ollama /api/embeddings。返回 list[list[float]]。"""
    host = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = model or os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    out: list[list[float]] = []
    for t in texts:
        body = json.dumps({"model": model, "prompt": t}).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out.append(json.loads(resp.read())["embedding"])
    return out


def ollama_embed_query(
    query: str,
    *,
    host: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
) -> list[float]:
    """单条 query embedding。返回 list[float]。"""
    return ollama_embed_passages([query], host=host, model=model, timeout=timeout)[0]


# ────────────────────────────────────────────────────────────────────
# 4. Path ACL（替代 kbchat.security.is_allowed 的 P1 子集）
# ────────────────────────────────────────────────────────────────────


# 兜底拒绝模式（与原 acl.is_path_allowed 行为一致）
_FORBIDDEN_PATH_PATTERNS = (".env", "/.ssh/", "/secrets/", "/etc/", "../")


def is_path_allowed(path: str | Path, *, allow_env_var: str = "P1_VAULT_ALLOWLIST") -> bool:
    """P1 路径 ACL。

    策略：
    1) 命中兜底拒绝模式 → False
    2) `allow_env_var`（缺省 P1_VAULT_ALLOWLIST）配置后，路径必须落在任一允许前缀下 → 否则 False
    3) 未配置 allowlist → True（默认放行；企业部署必须显式设）

    与 kbchat.security.is_allowed 行为对照：
    - kbchat 主要做 vault path 校验；P1 主要做"哪些目录允许被摄入"
    - 当 P1_VAULT_ALLOWLIST=逗号分隔绝对路径前缀 时启用严格模式
    """
    p = str(path)
    if p.startswith("~"):
        p = os.path.expanduser(p)

    # 1) 兜底拒绝
    if any(pat in p for pat in _FORBIDDEN_PATH_PATTERNS):
        return False

    # 2) allowlist 检查（可选）
    allowlist = os.environ.get(allow_env_var, "").strip()
    if allowlist:
        allowed_roots = [s.strip() for s in allowlist.split(",") if s.strip()]
        if not any(p.startswith(root) for root in allowed_roots):
            return False

    return True


__all__ = [
    "MarkdownHeadingSplitter",
    "Candidate",
    "pack_evidence",
    "ollama_embed_passages",
    "ollama_embed_query",
    "is_path_allowed",
]
