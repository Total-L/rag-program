# Mock Interview 脚本（self-run，20 分钟）

> 抽出 10 题，30 秒答 + 30 秒展开。

## 怎么跑

```bash
make mock-interview
# 或：python interview/mock_runner.py --sample 10
```

## 抽样题（10 道，覆盖所有 slice）

| # | slice | 题 | 时长 |
|---|---|---|---|
| 1 | theory | Q1: 什么是 RAG？为什么不用纯 LLM？ | 60s |
| 2 | theory | Q12: RRF 与线性融合区别？ | 60s |
| 3 | theory | Q20: Self-RAG / CRAG / Agentic 区别？ | 60s |
| 4 | production | Q31: p50/p95 延迟代表什么？ | 60s |
| 5 | production | Q36: RAG 攻击面有哪些？ | 60s |
| 6 | production | Q42: RAGAS 是什么？4 个核心指标？ | 60s |
| 7 | STAR | 故事 1：hybrid 提升 recall | 90s |
| 8 | STAR | 故事 2：引用幻觉修复 | 90s |
| 9 | STAR | 故事 3：ACL 漏网修复 | 90s |
| 10 | 设计 | "如果让你从零设计一个企业 RAG 系统，怎么开始？" | 120s |

---

## 自评表（每题后填）

```
Q[1-10]: ⭐⭐⭐⭐⭐（5 分制）
- 我答对了几条核心点？
- 我引用了具体数字 / 项目 / 代码吗？
- 我提到了 tradeoff 吗？
- 我能用 30 秒讲完吗？
```

## 评分标准

| 等级 | 标准 |
|---|---|
| ⭐⭐⭐⭐⭐ | 答对全部核心 + 引用项目 + 提 trade-off + 30 秒内 |
| ⭐⭐⭐⭐ | 答对核心 + 提到项目 |
| ⭐⭐⭐ | 答对核心，缺例子 |
| ⭐⭐ | 答对一半 |
| ⭐ | 答错 / 答不出 |

## 跑完后

- 把每题答案录下来（手机录音）→ 回放 → 找"嗯"、"那个"等口头禅
- 把"卡住"的题 → 写到 `interview/gaps.md`
- 下周再跑一次，对比进步

## 配套命令

```bash
# 抽 10 题
python interview/mock_runner.py --sample 10

# 抽 25 题
python interview/mock_runner.py --sample 25

# 全 50 题
python interview/mock_runner.py --all
```
