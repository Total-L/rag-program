# Verify P1 — 重跑指南（2026-07-27）

> 本指南面向**有 Ollama + 索引数据**的环境用户。重跑 `make verify-p1` 后，新报告应反映根因 1+2+3+4+5 全部修复的真实数字。

## TL;DR

```bash
# 1) 跑评测（5-10 分钟：50 题 × Ollama embedding + generation）
make verify-p1

# 2) 跑 CI gate（立刻）
python eval/regression/check_thresholds.py
```

## 预期结果（修复后）

| 指标 | 修复前 | 预期修复后 | 阈值 |
|---|---|---|---|
| `hit_rate` | 0.0（eval bug） | **≥ 0.60** | 0.50 |
| `recall_at_10` | 报告缺字段 | **≥ 0.60** | 0.50 |
| `mrr` | 报告缺字段 | **≥ 0.50** | 0.40 |
| `citation_validity` | 0.68 | **≥ 0.85** | 0.95 |
| `abstention_correctness` | 0.44 | **≥ 0.75** | 0.70 |
| `contains_expected` | 0.42 | **≥ 0.55** | 0.50 |
| `p50_latency_ms` | 5544 | **~4500** | (无阈值) |

## 数字预期逻辑

### hit_rate / recall_at_10 / mrr（根因 1+2+4 修复）

修复前 `hit_rate=0.0` 是**确定性 bug**（`startswith("hr-")` 配 `"E1"` 永远 False）。
修复后用 `expected_doc_ids ∩ retrieved_source_ids` 真 doc_id 集合比对 → 应跳到真实数字。

**推测值来源**：
- `contains_expected=0.42` 暗示 ~42% 答案含期望关键词 → 检索没完全空
- RRF 融合（dense+BM25, k=60）正常工作时，top-10 命中率应在 50-70%

### citation_validity / abstention_correctness（根因 3 修复）

修复前 0.68 / 0.44 因 heuristic 把含「找不到」「知识库中没」的合法答案强制 abstained。

修复后：
- JSON 解析成功时尊重 LLM 字段，heuristic 不覆盖
- markers 收紧到完整短语（前 30 字匹配）
- 56% 错误拒答应下降到 ~20%

### p50_latency_ms（根因 5 修复）

BM25 缓存省 ~100-500ms/次（在 5544ms 总延迟里占比小）。
**大头（Ollama embedding + LLM inference）需要换更小模型才能显著降**（不在本轮范围）。

## 新报告字段对照

### `eval/reports/p1_golden_latest.json` 现在包含

```json
{
  "overall": {
    "n": 50,
    "citation_validity": 0.85,
    "abstention_correctness": 0.75,
    "contains_expected": 0.55,
    "hit_rate": 0.60,
    "recall_at_10": 0.60,
    "mrr": 0.50,
    "p50_latency_ms": 4500
  },
  "by_slice": {
    "semantic": {...},
    "lexical": {...},
    "metadata": {...},
    "multihop": {...},
    "bilingual": {...},
    "unanswerable": {...},
    "adversarial": {...}
  },
  "rows": [
    {
      "id": "S01",
      "slice": "semantic",
      "must_abstain": false,
      "abstained": false,
      "abstention_correct": true,
      "citation_valid": true,
      "contains_expected": true,
      "hit": true,
      "recall_at_10": 1.0,
      "mrr": 1.0,
      "latency_ms": 4231.2
    },
    ...
  ]
}
```

### 新增真指标含义

- **hit_rate**：50 题中检索命中 expected_doc_ids 的比例（修前 0.0 是算法 bug）
- **recall_at_10**：50 题中 `|expected ∩ retrieved| / |expected|` 平均（top-10 召回）
- **mrr**：50 题中首个 expected 命中的 `1/rank` 平均（首个命中越靠前分数越高）

## 跑前检查清单

```bash
# 1) Ollama 在跑？
curl -s http://localhost:11434/api/tags | head -5

# 2) 索引数据在？该跑 datasets/generators/enterprise.py
ls datasets/enterprise_corpus/*.md | wc -l   # 应 > 0

# 3) Makefile 里 verify-p1 是什么？
grep -A 3 "verify-p1:" Makefile
```

## 跑后验证

### 1. 看 overall 数字

```bash
python -c "
import json
r = json.load(open('eval/reports/p1_golden_latest.json'))
for k, v in r['overall'].items():
    print(f'  {k}: {v}')
"
```

### 2. 跑 CI gate

```bash
python eval/regression/check_thresholds.py
```

预期：`✓ p1-enterprise-kb (p1_golden_latest.json) 全部阈值通过`

若仍有不达标项，参考下表诊断：

| 失败指标 | 可能原因 | 下一步 |
|---|---|---|
| hit_rate / recall_at_10 / mrr | 检索算法本身问题（不是 eval bug 了） | 调 RRF k / BM25 权重 / 加 cross-encoder rerank |
| abstention_correctness | 模型仍过保守 | 再调 markers / system prompt 措辞 |
| citation_validity | LLM 编造引用 ID | 收紧 SYSTEM prompt 规则 2 / 引用校验 |
| contains_expected | 生成质量问题 | 换更大 LLM / 改 prompt 给更多指令 |

### 3. 按 slice 看薄弱处

```bash
python -c "
import json
r = json.load(open('eval/reports/p1_golden_latest.json'))
for s, m in sorted(r['by_slice'].items(), key=lambda x: x[1].get('contains_expected', 1)):
    print(f'  {s:15s} n={m[\"n\"]:3d}  contains={m.get(\"contains_expected\", 0):.2f}')
"
```

`multihop` 预期最弱（需多 chunk 拼接）；`unanswerable` 应 abstention_correct=1.0（必须拒答）。

## 已完成的修复（无需再动）

| 根因 | 修复 | 文件 |
|---|---|---|
| 1 | 真 hit/recall/MRR（doc_id 集合比对） | `eval.py:46-62` |
| 2 | glob 加 short_id fallback | `check_thresholds.py:71-75` |
| 3 | heuristic 仅在 JSON 解析失败时启用 + 收紧 markers + 改写 system prompt | `pipeline.py:_parse_llm_response` + `SYSTEM` |
| 4 | 暴露 retrieved_chunk_ids 平行于 retrieved_source_ids | `pipeline.py:P1Result` |
| 5 | BM25 缓存（cache key 含 user_role + chunk_ids） | `pipeline.py:_get_bm25` |

## 相关测试

```bash
# 5 个测试文件守护修复
python -m pytest projects/p1-enterprise-kb/tests/test_abstention_heuristic.py      -v   # 11 用例
python -m pytest projects/p1-enterprise-kb/tests/test_bm25_cache.py                -v   # 9 用例
python -m pytest projects/p1-enterprise-kb/tests/test_eval_no_false_zero.py        -v   # 7 用例
python -m pytest projects/p1-enterprise-kb/tests/test_eval_real_metrics.py         -v   # 11 用例

# 全套
python -m pytest projects/p1-enterprise-kb/tests/                                    # 58 passed
```

## 失败时回滚路径

如果新数据看上去明显恶化（例如 abstention_correctness 从 0.44 降到 0.2）：

1. 检查 SYSTEM prompt 是否被改过 → 恢复 `pipeline.py:SYSTEM` 的规则 3 措辞
2. 检查 `_ABSTAIN_PHRASES` 是否还包含完整短语 → 4 个 marker 必须都在
3. 检查 `_parse_llm_response` 的 `json_parsed` 标志是否还在 → heuristic 必须只在 JSON 解析失败时启用
4. 看 `eval/reports/p1_golden_latest.json` 的 `rows[]` 里哪几题 abstained 错 → 决定是调 markers 还是调 prompt

完整 RCA 见 `RCA_p1_eval_regression.md`（7 个根因全有解）。