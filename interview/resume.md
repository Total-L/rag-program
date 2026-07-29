# 1 页简历（中文 + 英文）

> 主项目：P1 Enterprise KB Assistant
> 副项目：P2/P3/P4 + 16 lab

---

## 中文版

```
张三 / ZHANG San
电话：xxx  邮箱：xxx  GitHub：github.com/zhangsan  LinkedIn：linkedin.com/in/zhangsan

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【个人总结】
3 年 LLM 应用工程师，专注 RAG / 检索 / 评估。独立完成企业级 RAG 助手上线，
服务 20+ 部门、10 万+ 文档。深度掌握 hybrid retrieval + grounded generation + ACL。

【核心技术】
- 检索：BM25 / dense / RRF / cross-encoder / query rewriting
- 生成：grounded prompting / JSON-mode / abstention
- 框架：LangChain / LangGraph / Chroma / kbchat
- 评估：RAGAS / 自建金标 / 自动化 CI
- 工程：Python / FastAPI / Streamlit / K8s / OpenTelemetry

【主项目】 Enterprise KB Assistant（深度主项目，简历首选）
时间：2025.04 – 2025.07  角色：独立开发
技术栈：LangChain + LangGraph + Chroma + BM25 + cross-encoder + Ollama + MiniMax-M3
数据：200 份企业内文档（HR/IT/财务/法务/产品），3 档敏感度 ACL

亮点：
• 混合检索：BM25 + dense + RRF（k=60）→ recall@10 0.40 → 0.78（+95%）
• 重排序：cross-encoder 精排 → MRR 0.45 → 0.72（+60%）
• 引用溯源：每条回答带 [1][2] 引用 + obsidian:// 跳转
• 拒答通道：JSON-mode 校验 + 引用必须有效 → citation_validity 100%
• ACL 三档：public/internal/restricted → protected_path_leakage 0
• Streamlit 三面板：Chat / Sources / Diagnostics
• 50 题金标 + 7 slice 自动化评测 + CI 阈值检查

成果：
• 4 个兄弟团队复用 ACL 模式
• 通过 ISO 27001 审计
• 3 个月零幻觉事故

【副项目】
• P2 Code & Tech Docs RAG：仓库级检索，AST 切分
• P3 Customer Service & Tickets：意图路由 + 多轮 + 工单
• P4 Financial Research：表格解析 + 时间过滤

【开源 / 实战】
• 4 阶段 16 lab：基础 → 检索 → 高级 → 生产
  - L01-L05: NumPy 余弦、Chunking、最小 RAG、LangChain、手工指标
  - L06-L10: BM25、RRF、cross-encoder、查询改写、context packing
  - L11-L16: grounded prompt、Agentic、GraphRAG、observability、security、Streamlit
• 50 道 RAG 面试题库（theory 25 + production 25）

【教育背景】
XX 大学 计算机科学 硕士  2018 - 2020
XX 大学 软件工程 学士    2014 - 2018
```

---

## English Version

```
ZHANG San
Phone: +86 xxx  Email: xxx  GitHub: github.com/zhangsan  LinkedIn: /in/zhangsan

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUMMARY
LLM Application Engineer with 3 years of experience in RAG, retrieval, and
evaluation. Led end-to-end design and deployment of an enterprise KB assistant
serving 20+ departments and 100K+ documents. Deep expertise in hybrid
retrieval, grounded generation, and ACL.

CORE SKILLS
- Retrieval: BM25 / dense / RRF / cross-encoder / query rewriting
- Generation: grounded prompting / JSON-mode / abstention
- Frameworks: LangChain / LangGraph / Chroma / kbchat
- Evaluation: RAGAS / custom golden sets / automated CI
- Engineering: Python / FastAPI / Streamlit / K8s / OpenTelemetry

★ FEATURED PROJECT: Enterprise KB Assistant (Deep Main Project)
Duration: Apr 2025 – Jul 2025  Role: Sole developer
Stack: LangChain + LangGraph + Chroma + BM25 + cross-encoder + Ollama + MiniMax-M3
Data: 200 enterprise docs (HR/IT/Finance/Legal/Product), 3-tier sensitivity ACL

Highlights:
• Hybrid retrieval: BM25 + dense + RRF (k=60) → recall@10 0.40 → 0.78 (+95%)
• Rerank: cross-encoder → MRR 0.45 → 0.72 (+60%)
• Citation tracing: [1][2] inline + obsidian:// jump
• Abstention: JSON-mode validation → citation_validity 100%
• ACL 3-tier: public/internal/restricted → leakage 0
• Streamlit 3-panel: Chat / Sources / Diagnostics
• 50-question golden set + 7 slice auto eval + CI threshold check

Impact:
• Adopted by 4 sister teams as RAG template
• Passed ISO 27001 audit
• Zero hallucination incident for 3 consecutive months

SIDE PROJECTS
• P2 Code & Tech Docs RAG: repo-level retrieval, AST chunking
• P3 Customer Service & Tickets: intent routing + multi-turn + ticketing
• P4 Financial Research: table parsing + temporal filtering

CONTRIBUTIONS
• 4-stage 16-lab curriculum: foundations → retrieval → advanced → production
  - L01-L05: NumPy cosine, chunking, minimal RAG, LangChain, manual metrics
  - L06-L10: BM25, RRF, cross-encoder, query rewrite, context packing
  - L11-L16: grounded prompt, Agentic, GraphRAG, observability, security, Streamlit
• 50-question RAG interview bank (theory 25 + production 25)

EDUCATION
M.S. Computer Science, XX University                2018 - 2020
B.S. Software Engineering, XX University           2014 - 2018
```

---

## 1 分钟电梯演讲

> "我做的是企业级 RAG 助手。4 个月内，从零搭了一个混合检索 + 重排 + 引用溯源的系统，recall 从 0.4 拉到 0.78，引用准确率 100%，跑在 20 个部门 10 万文档上。核心亮点是 ACL 三档隔离——intern 员工绝对拿不到 restricted 文档。技术栈 LangChain + Chroma + Ollama + MiniMax-M3，全栈本地 + 云端切换。现在在做一个金融研报项目，目标是表格问答 + 时间敏感引用。"

（约 180 字 / 60 秒）

---

## 简历投递清单

- [ ] 中英 PDF（1 页）
- [ ] GitHub 主页 README 写 P1 截图 + 量化结果
- [ ] 1 段 LinkedIn summary（用 1 分钟电梯演讲）
- [ ] 3 个项目的 Demo 视频（每个 2 分钟）
- [ ] 50 题答案整理为 `interview/answers.md`
