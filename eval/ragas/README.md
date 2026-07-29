# eval/ragas/ — C1 真 RAGAS 评测 (ENTERPRISE_AUDIT C1)

## 状态

| 项 | 值 |
|---|---|
| 框架 | **ragas-protocol-compatible shim**(实现与 RAGAS 0.2.x API 同型) |
| 库版本(target) | `ragas>=0.2,<0.3`(已声明在 `pyproject.toml`,安装受阻时降级到 shim) |
| LLM 后端 | `mmx`(默认,经 `MmxRagasLLM`);可 env 切到 ollama |
| 测试覆盖 | 23(全部 PASSED,见 `tests/test_ragas_metrics.py`) |

## 为什么是 shim

- 真 `pip install ragas>=0.2,<0.3` 当前在开发机失败:`langsmith 0.10.10` + `pyarrow 25.0.0` wheels 下载超时(connection error / read timeout)
- C1 不要阻塞在网络,实现 RAGAS 协议的子集,签名与真库同型
- 当 `ragas` 装上后,`from ragas.llms import BaseRagasLLM` 可一行替换 shim

## 公开 API

```
from eval.ragas import (
    MmxRagasLLM,              # LLM(与 ragas.llms.BaseRagasLLM 同型)
    faithfulness,             # RAGAS 0.2.x semantics
    answer_relevancy,
    context_precision,
    context_recall,
    evaluate,                 # batch facade(sim of ragas.evaluate)
    ndcg_at_k,                # 检索层指标(纯 stdlib)
    ndcg_at_5,
    ndcg_at_10,
    unsupported_claims_rate,  # 自定义:列出无 context 支撑的声明
    hallucination_rate,       # 1 - faithfulness
    numeric_hallucination_rate, # 纯 stdlib:answer 中的数字在 context 中找不到的比例
    runner,                   # CLI:runs real RAGAS eval
    compare,                  # CLI:A/B pipeline config compare
)
```

## 4 个 RAGAS 核心指标语义

### 1. faithfulness
- **问**:答案是否基于 context?
- **实现**:把 answer 拆 statements → 每个 statement 问 LLM "支持/不支持" → ratio
- **严格度**:LLM 解析失败 → 0(不静默填 1)

### 2. answer_relevancy
- **问**:答案对问题有多切题?
- **实现**:LLM judge 0..5 → 归一到 0..1

### 3. context_precision
- **问**:检索的上下文对回答 ground_truth 有多有用?
- **实现**:每个 context LLM judge "相关/不相关" → ratio

### 4. context_recall
- **问**:ground_truth 拆 statements,每个是否被 context 覆盖?
- **实现**:每个 statement 问 LLM "可推出/不可" → ratio

## 检索层指标

- `ndcg_at_k(retrieved_source_ids, expected_doc_ids, k)`
- 二值 graded relevance(在 expected 集合 → 1,否则 0)
- IDCG 用 perfect rank 计算
- 边界:空 → 0.0;全部命中 → 1.0;部分 → 0..1

## 幻觉检测三个口径

| 指标 | 算法 | 依赖 |
|---|---|---|
| `hallucination_rate` | `1 - RAGAS faithfulness` | mmx LLM |
| `unsupported_claims_rate` | 让 LLM 列声明 + 标 unsupported 数 | mmx LLM |
| `numeric_hallucination_rate` | regex 抽 answer 数字/日期,核对 context | 纯 stdlib |

## CLI 用法

### 单项目跑 RAGAS:
```bash
python -m eval.ragas.runner --project p1-enterprise-kb \
  --metrics faithfulness,answer_relevancy \
  --n 5      # mini-eval(无 ragas_input.jsonl 时)
```
输出: `eval/reports/ragas_<project>_<ts>.json`

### A/B Pipeline 对比:
```bash
# 准备两份 yaml
cat > /tmp/cfg_a.yml <<'EOF'
chunk_size: 400
top_k: 8
reranker: heuristic
EOF
cat > /tmp/cfg_b.yml <<'EOF'
chunk_size: 600
top_k: 12
reranker: cross-encoder
EOF

python -m eval.ragas.compare --compare /tmp/cfg_a.yml /tmp/cfg_b.yml --n 50
```
输出: `eval/reports/ab_<ts>.json` + 控制台 markdown 表格(每指标 a/b/Δ/better)

### 接 P1 eval(已有流水线):
```bash
# 跑 P1 50 题金标,新指标 ndcg_at_5/ndcg_at_10 自动加入 overall
cd projects/p1-enterprise-kb
python tests/run_eval.py
# 加 hallucination(opt-in,L1-judged 慢)
P1_EVAL_HALLUCINATION=1 python tests/run_eval.py
```

## 阈值门(`/eval/regression/check_thresholds.py`)

新指标加入 `overall` 后,阈值门**自动兼容**(它读 `report["overall"]` 任何字段,缺字段 → "报告缺少该指标" 非阻塞)。

目前(2026-07):
- hit_rate ≥ 0.50
- recall_at_10 ≥ 0.50
- mrr ≥ 0.40
- citation_validity ≥ 0.95
- abstention_correctness ≥ 0.70
- contains_expected ≥ 0.50
- ndcg_at_5 / ndcg_at_10 — **未设阈值**(待 1–2 周观测分布后加)

## 已知边界 (Per CLAUDE.md "Fail Loud")

1. **shim 而非真 ragas**:与 RAGAS 0.2.x 同型但手工实现,完全相同的指标名 ≠ 完全相同的算法细节
2. **mmx 后端不可用**:`MmxRagasLLM` 失败兜底返回 "" → 上层指标 = 0(strict);生产环境若想"温和降级",把指标层的 strict 改 warn
3. **LLM judge 评分波动**:每次 prompt + 0.0 temperature 但仍可能 ±0.05 抖动,跑测试时通常 5 题平均才稳定
4. **RAGAS faithfulness 与 nDCG 不对称**:nDCG 是检索层(per-row,cheap),faithfulness 是生成层(aggregate,LLM-call);不要混用

## 测试

```
pytest projects/p1-enterprise-kb/tests/test_ragas_metrics.py -v
```
- 23/23 PASSED
- 覆盖:nDCG 边界、MmxRagasLLM 调用约定、faithfulness / answer_relevancy / hallucination、compare markdown 渲染
- 全部 LLM 调用 `patch("subprocess.run")`,无网络依赖
