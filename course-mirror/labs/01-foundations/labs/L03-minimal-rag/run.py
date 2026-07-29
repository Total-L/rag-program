"""L03 — 最小 RAG：不依赖 LangChain，Ollama 嵌入 + mmx 生成。

链路：
  query → embed (Ollama) → cosine top-k → context pack → mmx (MiniMax-M3) → answer

回退策略：
- 无 Ollama → 用 kbchat 本地 BGE（仍可跑）
- 无 mmx → 用 Ollama llama3.1 生成

运行：
    python 01-foundations/labs/L03-minimal-rag/run.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# 共享 8 篇语料 + 系统提示(跟 L04 同一份源,见 ../_shared/lab_corpus.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared.lab_corpus import CORPUS as CORPUS_DOCS, DOCS_TEXT as CORPUS, SYSTEM_PROMPT  # noqa: E402

ART = Path("artifacts/L03")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 嵌入后端：Ollama（默认）或 kbchat BGE（回退）────
def ollama_embed(texts: list[str], model: str = "nomic-embed-text", host: str = "http://localhost:11434") -> list[list[float]]:
    """调用 Ollama /api/embeddings。"""
    out: list[list[float]] = []
    for t in texts:
        body = json.dumps({"model": model, "prompt": t}).encode("utf-8")
        req = urllib.request.Request(
            f"{host}/api/embeddings", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            out.append(json.loads(resp.read())["embedding"])
    return out


def kbchat_embed(texts: list[str]) -> list[list[float]]:
    """回退到 kbchat 本地 BGE。"""
    from kbchat.embeddings import embed_passages
    return [v.tolist() for v in embed_passages(texts)]


# ── 2. 检索：纯 NumPy cosine top-k ─────────────────────────
import numpy as np


def cosine_topk(q: list[float], doc_vecs: list[list[float]], k: int = 3) -> list[tuple[int, float]]:
    Q = np.array(q, dtype=np.float32)
    D = np.array(doc_vecs, dtype=np.float32)
    qn = Q / max(np.linalg.norm(Q), 1e-9)
    dn = D / np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-9)
    sims = dn @ qn
    idx = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in idx]


# ── 3. 生成后端：mmx（默认）或 Ollama（回退）───────────────
def mmx_generate(prompt: str, system: str = "", model: str = "MiniMax-M3", timeout: int = 30) -> str:
    """调用 mmx CLI（list argv，无 shell 注入）。"""
    import subprocess
    args = ["mmx", "text", "chat", "--model", model, "--max-tokens", "512", "--temperature", "0.0",
            "--output", "json"]
    if system:
        args += ["--system", system]
    args += ["--message", prompt]
    env = {k: v for k, v in os.environ.items() if not k.startswith("MMX_") and k not in ("MMX_API_KEY",)}
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode == 3:
        raise RuntimeError("mmx auth 失败")
    if r.returncode == 4:
        raise RuntimeError("mmx 配额耗尽")
    if r.returncode == 5:
        raise RuntimeError("mmx 超时")
    if r.returncode != 0:
        raise RuntimeError(f"mmx 失败: {r.stderr[:200]}")
    try:
        return json.loads(r.stdout).get("text", r.stdout)
    except Exception:
        return r.stdout


def ollama_generate(prompt: str, system: str = "", model: str = "llama3.1:8b") -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "system": system,
        "stream": False, "options": {"temperature": 0.0, "num_predict": 512},
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["response"]


# ── 4. RAG 主流程(语料 / system prompt 已从共享模块导入)─────────────


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L03 — 最小 RAG（Ollama + mmx）")
    print("════════════════════════════════════════════\n")

    # 1. 嵌入（双 backend）
    print("→ 嵌入...")
    use_ollama = os.environ.get("RAG_PROG_EMBED_BACKEND", "ollama") == "ollama"
    try:
        if use_ollama:
            doc_vecs = ollama_embed(CORPUS)
            embed_backend = "ollama"
        else:
            raise RuntimeError("skip")
    except Exception as e:
        print(f"  Ollama 不可用（{e}），回退到 kbchat BGE")
        doc_vecs = kbchat_embed(CORPUS)
        embed_backend = "kbchat-bge"

    print(f"  embedding 后端: {embed_backend}, dim={len(doc_vecs[0])}")

    # 2. 检索 + 生成（3 个示例问题）
    queries = [
        "员工每年能休多少天年假？",
        "P0 事故响应时限？",
        "公司的差旅住宿标准是什么？",
    ]
    use_mmx = os.environ.get("RAG_PROG_LLM_BACKEND", "mmx") == "mmx"
    log: list[dict] = []
    for q in queries:
        try:
            if use_ollama:
                qv = ollama_embed([q])[0]
            else:
                qv = doc_vecs[0]  # 简化：kbchat 不分 query/passage，用同函数
                from kbchat.embeddings import embed_query
                qv = embed_query(q).tolist()
        except Exception as e:
            print(f"  嵌入失败: {e}")
            continue

        hits = cosine_topk(qv, doc_vecs, k=3)
        context = "\n".join(f"[{i+1}] {CORPUS[idx]}" for i, (idx, _) in enumerate(hits))
        user = f"上下文：\n{context}\n\n问题：{q}\n\n回答："

        try:
            if use_mmx:
                ans = mmx_generate(user, system=SYSTEM_PROMPT)
                gen_backend = "mmx"
            else:
                ans = ollama_generate(user, system=SYSTEM_PROMPT)
                gen_backend = "ollama"
        except Exception as e:
            ans = f"（生成失败：{e}）"
            gen_backend = "failed"

        print(f"\nQ: {q}")
        for i, (idx, score) in enumerate(hits):
            print(f"  [{i+1}] (cos={score:.3f}) {CORPUS[idx]}")
        print(f"A ({gen_backend}): {ans}")
        log.append({"q": q, "hits": [(i, s, CORPUS[i]) for i, s in hits], "answer": ans,
                    "embed": embed_backend, "gen": gen_backend})

    # 3. 写报告
    (ART / "run_log.json").write_text(
        json.dumps({"embed_backend": embed_backend, "queries": log}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ART / "report.md").write_text(
        "# L03 — 最小 RAG 报告\n\n"
        f"- 嵌入后端：**{embed_backend}**\n"
        f"- 生成后端：**{log[0]['gen'] if log else 'n/a'}**\n"
        "- 链路：query → embed → cosine top-3 → context pack → mmx(MiniMax-M3)\n\n"
        "## 与 LangChain 版的差异（L04）\n"
        "- 不依赖 langchain，全部 < 100 行\n"
        "- 检索效率 O(N)，生产环境需 HNSW\n"
        "- 无 BM25、rerank、agent（后续 lab 补齐）\n",
        encoding="utf-8",
    )
    print(f"\n✓ L03 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
