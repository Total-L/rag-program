"""L13 — GraphRAG：基于 [[wikilinks]] 构建图，处理多跳问答。

跑：
    python 03-advanced-production/labs/L13-graph-rag/run.py

简化：把 markdown 里的 [[wiki link]] 当边，构建有向图；
2 跳邻域作为多跳上下文。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path

ART = Path("artifacts/L13")
ART.mkdir(parents=True, exist_ok=True)

# 5 个互联文档（toy）
DOCS = {
    "d1": "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天。详见 [[d2]]。",
    "d2": "司龄计算：入职日起累计满 12 个月算 1 年。参考 [[d3]] 和 [[d4]]。",
    "d3": "病假：参考 [[d1]] 的 OA 流程。",
    "d4": "差旅：跨城出差见 [[d5]]。",
    "d5": "P0 事故：30 分钟内 mitigation。",
}
WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


def build_graph(docs: dict[str, str]) -> dict[str, set[str]]:
    g: dict[str, set[str]] = defaultdict(set)
    for src, body in docs.items():
        for tgt in WIKI_RE.findall(body):
            if tgt in docs:
                g[src].add(tgt)
                g[tgt].add(src)  # 无向
    return dict(g)


def bfs_neighborhood(graph: dict[str, set[str]], start: str, max_hops: int = 2) -> list[str]:
    visited: set[str] = set()
    q: deque[tuple[str, int]] = deque([(start, 0)])
    while q:
        node, hop = q.popleft()
        if node in visited or hop > max_hops:
            continue
        visited.add(node)
        for nb in graph.get(node, []):
            if nb not in visited:
                q.append((nb, hop + 1))
    return sorted(visited)


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L13 — GraphRAG（基于 wikilinks）")
    print("════════════════════════════════════════════\n")

    g = build_graph(DOCS)
    print("图（邻接表）：")
    for k, v in g.items():
        print(f"  {k} → {sorted(v)}")

    # 多跳查询
    for start in ["d1", "d3", "d5"]:
        nb = bfs_neighborhood(g, start, max_hops=2)
        print(f"\n从 {start} 出发 2 跳可达：{nb}")
        print(f"  上下文：")
        for n in nb:
            print(f"    [{n}] {DOCS[n]}")

    # 复用 networkx
    try:
        import networkx as nx
        G = nx.Graph()
        for s, tgts in g.items():
            for t in tgts:
                G.add_edge(s, t)
        print(f"\n→ networkx: {G.number_of_nodes()} 节点 {G.number_of_edges()} 边")
        for start in ["d1"]:
            for path in nx.all_simple_paths(G, start, "d5", cutoff=3):
                print(f"  d1 → d5 路径: {path}")
    except ImportError:
        print("\n⚠ networkx 未安装")

    # 复用 kbchat
    try:
        from kbchat.loaders.obsidian import iter_notes
        print("\n→ kbchat.loaders.obsidian.iter_notes 可用（生产环境）")
    except Exception as e:
        print(f"\n⚠ kbchat 不可用：{e}")

    (ART / "graph.json").write_text(
        json.dumps({k: sorted(v) for k, v in g.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (ART / "report.md").write_text(
        """# L13 — GraphRAG 报告

## 思路
把 markdown 里的 [[wikilink]] 当作图边，2-3 跳邻域作为多跳上下文。

## 适用
- 知识库文档互相引用强（Obsidian vault 典型）
- 多跳 QA（如 X 的负责人是谁？负责 X 的 Y 是谁？）

## 不适用
- 文档间无引用
- 大多数简单事实查询（直接 dense 就够）

## 进阶：MS GraphRAG
微软的 GraphRAG：实体抽取 → 社区检测 → 摘要。
本 lab 简化版只是图遍历，够入门理解。
""",
        encoding="utf-8",
    )
    print(f"\n✓ L13 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
