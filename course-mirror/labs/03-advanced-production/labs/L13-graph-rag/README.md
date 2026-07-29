# L13 — GraphRAG

## 学习目标
- 看到基于 `[[wikilinks]]` 构建图
- 看到多跳邻域作为上下文
- 理解 GraphRAG 的适用场景

## 跑
```bash
make lab-L13
```

## 思路
把 markdown 里的 `[[wikilink]]` 当作图边，2-3 跳邻域作为多跳上下文。

## 适用
- 知识库文档互相引用强（Obsidian vault 典型）
- 多跳 QA（"X 的负责人是谁？负责 X 的 Y 是谁？"）

## 进阶：MS GraphRAG
微软的 GraphRAG：实体抽取 → 社区检测 → 摘要。
本 lab 简化版只是图遍历，够入门理解。
