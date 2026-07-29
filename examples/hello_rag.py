"""Hello RAG — 30 行最小例子。

跑：
    python examples/hello_rag.py

依赖：仅 urllib（调 Ollama embedding）。
无 kbchat 依赖。
"""
import json
import os
import urllib.request
from pathlib import Path

# ── 1. 一份语料 ─────────────────────────────────────────
CORPUS = [
    "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天，3-5 年 15 天，5+ 年 20 天。",
    "差旅住宿标准：一线城市 800 元/晚，二线 600 元，三线 400 元。",
    "P0 事故 30 分钟内 mitigation，24 小时内 RCA。",
    "限制性股票分 4 年 vest，cliff 1 年。",
    "VPN 接入：vpn.corp.example.com，首次登录需绑定手机令牌。",
]

# ── 2. 调用 Ollama Embedding ─────────────────────────────
def embed(text: str) -> list[float]:
    body = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/embeddings", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["embedding"]


# ── 3. 索引语料 ─────────────────────────────────────────
import numpy as np
doc_vecs = [embed(d) for d in CORPUS]


# ── 4. 余弦 top-3 ───────────────────────────────────────
def search(q: str, k: int = 3) -> list[tuple[int, float]]:
    qv = np.array(embed(q))
    D = np.array(doc_vecs)
    qn = qv / max(np.linalg.norm(qv), 1e-9)
    dn = D / np.maximum(np.linalg.norm(D, axis=1, keepdims=True), 1e-9)
    sims = (dn @ qn).tolist()
    idx = sorted(range(len(sims)), key=lambda i: -sims[i])[:k]
    return [(int(i), float(sims[i])) for i in idx]


# ── 5. 调 LLM（Ollama）── 拼 context → 生成答案 ──────
def ask(q: str) -> str:
    hits = search(q)
    context = "\n".join(f"[{i+1}] {CORPUS[idx]}" for i, (idx, _) in enumerate(hits))
    prompt = f"基于以下证据回答问题，每条事实用 [编号] 标注。\n\n证据：\n{context}\n\n问题：{q}\n\n回答："
    body = json.dumps({
        "model": "llama3.2:1b",
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 256},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["response"]


# ── 6. 跑 ─────────────────────────────────────────────
if __name__ == "__main__":
    for q in ["员工每年能休多少天年假？", "P0 事故如何处理？", "如何访问公司内网？"]:
        print(f"\nQ: {q}")
        print(f"A: {ask(q)}")
