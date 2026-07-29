# L04 对照实验 — L03 (手写) vs L04 (LangChain LCEL)

语料:8 篇闭域文档(见 `01-foundations/labs/_shared/lab_corpus.py`)。
题库:8 题(`01-foundations/labs/_shared/lab_golden.jsonl`)。
评估:`eval/ragas/run.py` 的 4 个 fake-RAGAS 指标 + 自定义 retrieval/answer/abstain/latency。

## Overall(全部 8 题)

| 指标 | L03 手写 | L04 LangChain | Δ |
|---|---|---|---|
| `n` | 8 | 8 | — |
| `retrieval_recall_avg` | 0.5 | 0.625 | +0.125 |
| `retrieval_correct_rate` | 0.5 | 0.625 | +0.125 |
| `contains_expected_rate` | 0.625 | 0.375 | -0.25 |
| `abstention_correct_rate` | 0.625 | 0.375 | -0.25 |
| `p50_latency_ms` | 2080.1 | 511.9 | -1568.2 |
| `ragas_faithfulness_avg` | 0.375 | 0.125 | -0.25 |
| `ragas_answer_relevancy_avg` | 0.25 | 0.125 | -0.125 |
| `ragas_context_precision_avg` | 0.375 | 0.375 | +0.0 |
| `ragas_context_recall_avg` | 0.5 | 0.625 | +0.125 |

## Per-slice

### slice=semantic

| 指标 | L03 | L04 |
|---|---|---|
| retrieval_recall_avg | 0.5714 | 0.7143 |
| contains_expected_rate | 0.5714 | 0.2857 |
| abstention_correct_rate | 0.5714 | 0.2857 |

### slice=unanswerable

| 指标 | L03 | L04 |
|---|---|---|
| retrieval_recall_avg | 0.0 | 0.0 |
| contains_expected_rate | 1.0 | 1.0 |
| abstention_correct_rate | 1.0 | 1.0 |

---

> ⚠️ fake-RAGAS:`eval/ragas/run.py` 的 4 个 scorer 是手写的简化版,
> 不是真 `ragas` 包;只是用来给两个 pipeline 出可比指标。
> 真要 ragas 包需 `pip install ragas` 并配 LLM judge,见 plan 范围外。
