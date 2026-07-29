# L13 — GraphRAG 报告

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
