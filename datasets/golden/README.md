# 金标集 v1 — 50 题

> 与课程 kbchat 库 `QuestionSlice` 枚举对齐（semantic / lexical / metadata / multihop / bilingual / unanswerable / adversarial）。

## 切片分布

| slice | 数量 | 难度 | 关键评测能力 |
|---|---|---|---|
| semantic | 12 | medium | 语义匹配、paraphrase |
| lexical | 8 | easy | 关键词命中 |
| metadata | 8 | hard | 过滤、结构化查询 |
| multihop | 8 | hard | 跨文档推理 |
| bilingual | 6 | medium | 中英混合 |
| unanswerable | 5 | medium | 拒答正确 |
| adversarial | 3 | hard | prompt injection 防御 |

## 字段说明

```json
{
  "id": "S01",
  "question": "员工每年能休多少天年假？",
  "slice": "semantic",
  "expected_doc_ids": ["hr-001"],
  "expected_answer_contains": ["年假", "5 天"],
  "must_abstain": false,
  "difficulty": "medium",
  "language": "zh",
  "notes": "司龄 <1 年 5 天"
}
```

- `expected_doc_ids`：期望命中的源文档 ID（与生成器 seed=42 对齐）
- `expected_answer_contains`：答案应包含的关键词（任何匹配即通过）
- `must_abstain`：true 则必须拒答，命中反而扣分
- `notes`：评分细节

## 使用

```bash
# 生成（写入 datasets/golden/golden_v1.jsonl）
python -m datasets.golden.build_v1

# 跑评测（在 P1 项目内）
make verify-p1
```

## 与课程共享

本文件与 `rag-course` 训练营的 golden 集字段兼容（QuestionSlice 枚举对齐）；
如需双向引用，做一次字段映射即可。本工作区**不读取**训练营的金标文件。
