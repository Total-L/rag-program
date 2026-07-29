# L09 — 查询改写 4 策略

## 学习目标
- 看到 multi-query / HyDE / step-back / decomposition 4 种改写
- 理解每种策略的适用场景与成本

## 跑
```bash
make lab-L09
```

## 4 策略
| 策略 | 思路 | 适用 | 成本 |
|---|---|---|---|
| multi-query | 生成 N 个 paraphrased | 短/含糊问题 | N× 检索 + 1× LLM |
| HyDE | 生成假想答案再检索 | 知识领域与措辞差距大 | 1× 检索 + 1× LLM |
| step-back | 抽象一层 | 用户问细节但 KB 是概览 | 2× 检索 |
| decomposition | 拆多跳 | 多跳 QA | N× 检索 |
