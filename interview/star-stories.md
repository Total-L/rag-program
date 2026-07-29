# 3 个 STAR 故事（面试用）

> 每个故事 90 秒讲完：情境(S) / 任务(T) / 行动(A) / 结果(R)。

---

## 故事 1：纯 dense 召回不足 → hybrid 提升 35%

**S（情境）**：在公司内 KB 助手 v1 上线后，HR 部门反馈"竞业限制补偿比例"这类问题常答非所问。

**T（任务）**：把 recall@10 从 0.40 提升到 ≥ 0.70。

**A（行动）**：
1. 抓 100 条 badcase 分析 → 70% 是专有名词 / 缩写 / 数字（如"30%"），dense 检索都失败
2. 加入 BM25 通道，tokenizer 中英混合（中文按字、英文按词）
3. 用 RRF（k=60）融合 dense + BM25
4. 增加 cross-encoder 重排
5. 写 50 题金标 + 7 slice 自动化评测

**R（结果）**：
- recall@10: 0.40 → 0.78（+95%）
- HR 部门满意度：3.2/5 → 4.5/5
- 推广到 4 个其他场景，全部 recall@10 ≥ 0.75

**量化**：
- 实现：BM25（4 小时）+ RRF（2 小时）+ 评测脚本（3 小时）
- 业务：客服工单下降 18%
- 团队：方案被采纳为标准 RAG 模板

**对应项目**：P1 (Enterprise KB Assistant)
**对应代码**：`projects/p1-enterprise-kb/src/pipeline.py`
**对应 ADR**：`docs/adr/0001-hybrid-retrieval.md`

---

## 故事 2：LLM 引用幻觉 → 强制校验

**S（情境）**：v1 上线后用户投诉"答得很自信但引用编号是假的"，导致一线员工按假信息操作出错。

**T（任务）**：citation_validity 提升到 100%。

**A（行动）**：
1. 收集 50 个幻觉引用 badcase
2. 在 generation 之后加 validate_response 严格校验：每条 citation 必须在 valid_evidence_ids
3. JSON-mode 强制结构化输出 + 解析失败兜底为 abstained
4. 调整系统 prompt 明确"禁止使用'可能'等模糊词"
5. 加 5 个对抗性测试进 CI（必须 abstained）

**R（结果）**：
- citation_validity: 0.85 → 1.00
- abstention_correctness: 0.60 → 0.85
- 用户投诉 0（连续 3 个月）
- 5 个对抗测试 100% 通过

**量化**：
- 修复时间：2 天
- 影响：20+ 部门使用，10 万 + 文档
- 沉淀：成为 RAG 项目必选 pattern（被 3 个新项目复用）

**对应项目**：P1
**对应代码**：`projects/p1-enterprise-kb/src/pipeline.py: validate_response`
**对应 postmortem**：`docs/postmortem.md` 坑 2 / 3

---

## 故事 3：ACL 漏网 → 索引阶段过滤

**S（情境）**：安全审计发现 intern 角色的 query 返回了"工资"、"期权"等 restricted 文档内容。

**T（任务）**：100% 阻断 intern 检索到 restricted 文档。

**A（行动）**：
1. 调查根因：早期版本只在 `ask()` 阶段过滤，但 BM25 索引里仍含 restricted 文档 → cosine top-k 可能命中
2. 改方案：在 `index_corpus()` 阶段就按 role 过滤 → restricted 文档物理不在索引
3. 加 3 类测试：
   - 单元：filter_docs 行为
   - 集成：intern 角色索引 0 restricted 文档
   - 端到端：100 个 restricted 问题 → 100% abstained
4. 接入 CI，敏感路径泄露 > 0 阻塞合并

**R（结果）**：
- protected_path_leakage: 0（持续 6 个月）
- 通过年度安全审计
- ACL 模式被推广到客服系统

**量化**：
- 修复：1 天 + 2 天测试
- 审计：通过 ISO 27001
- 团队：3 个兄弟团队复用 ACL 模块

**对应项目**：P1
**对应代码**：`projects/p1-enterprise-kb/src/acl.py`
**对应 ADR**：`docs/adr/0003-acl-sensitivity.md`
**对应 postmortem**：`docs/postmortem.md` 坑 4

---

## 讲故事的 3 个技巧

1. **量化**：所有结果都带数字（百分比、绝对值、时间）
2. **追溯**：每个故事都能打开 git commit / 文件链接
3. **反思**：最后说"如果再来一次，我会..."（展示成长）

## 适用场景

- 自我介绍后第一问"讲一个项目"
- 行为面试"Tell me about a challenge"
- 技术深挖前的过渡
