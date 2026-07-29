# 50 道 RAG 面试题（theory 25 + production 25）

> 每题 30 秒能答完 + 30 秒能展开。附"对应项目/代码片段"。

---

## Ⅰ. Theory（25 题）

### 基础概念

**Q1. 什么是 RAG？为什么不用纯 LLM？**
A. Retrieval-Augmented Generation：先检索相关文档，再让 LLM 基于检索结果回答。
   vs 纯 LLM：知识固化、不能更新、容易幻觉；RAG 注入新知识 + 引用溯源。
   *对应：L03 / L04*

**Q2. Embedding 是什么？为什么不直接用 bag-of-words？**
A. Embedding = 把文本映射到稠密向量，捕获语义相似度。
   vs BoW：维度小、无语义、无法识别 paraphrase。
   *对应：L01*

**Q3. 余弦相似度 vs 点积 vs L2 距离？**
A. cosine：向量夹角，对长度不敏感（已 L2 归一化时退化为点积）。
   L2：欧氏距离，受长度影响。
   一般 dense 检索 L2 归一化 + 内积。
   *对应：L01*

**Q4. HNSW 与 IVF 的区别？什么时候用谁？**
A. HNSW：图索引，O(log N)，内存大；适合 < 10M 文档。
   IVF-PQ：倒排 + 量化，O(√N)，磁盘友好；适合 > 10M 文档。
   *对应：L01 进阶 / L08*

**Q5. Top-k 检索的时间复杂度？O(N) 为什么不能接受？**
A. O(N) 比较。N=1M 时单 query 100ms+，无法实时。
   HNSW 把比较降到 O(log N)。
   *对应：L01*

### Chunking

**Q6. Chunking 为什么重要？**
A. 太大：噪声多、recall 低；太小：上下文缺、LLM 失忆。
   经验：中文 200–400 字，英文 200–500 token。
   *对应：L02*

**Q7. Fixed / Recursive / MarkdownHeading 何时用？**
A. Fixed：仅 schema 化文本。
   Recursive：聊天/邮件，按段落 + 句号 fallback。
   MarkdownHeading：MD 文档，保留 heading_path。
   *对应：L02*

**Q8. chunk overlap 的作用？多少合适？**
A. 避免边界信息丢失。10–20% chunk_size。
   太低：边界答案漏；太高：检索冗余、embedding 噪声。
   *对应：L02*

**Q9. late chunking 是什么？与普通 chunking 区别？**
A. 先把整文档编码成长向量，再按 token 切分得到 chunk embedding。
   vs 普通：先切再 encode → 失去跨段上下文。
   适合长文档。
   *对应：L02 进阶*

### 检索

**Q10. 什么是 BM25？与 TF-IDF 区别？**
A. BM25 = 改良 TF-IDF：(tf·(k1+1)) / (tf + k1·(1-b+b·|d|/avgdl))。
   BM25 多了长度归一化 + tf 饱和。
   *对应：L06*

**Q11. 为什么 dense + sparse 混合通常更好？**
A. dense 强语义、sparse 强精确词；互补。
   召回从 0.40 提升到 0.75（实验观察）。
   *对应：L07 / P1 postmortem*

**Q12. RRF 与线性融合（concat、max）区别？**
A. RRF：按排名倒数求和，对分数绝对值不敏感。
   线性融合：需要归一化，对极端值敏感。
   RRF 更鲁棒。
   *对应：L07*

**Q13. cross-encoder 与 bi-encoder 区别？何时用谁？**
A. bi-encoder：q, d 分别编码 → 内积 → 快但粗。
   cross-encoder：(q, d) 一起编码 → 慢但精。
   实践：bi-encoder 召回 top-100，cross-encoder 重排 top-5。
   *对应：L08*

**Q14. 查询改写 4 策略及适用场景？**
A. multi-query：短/含糊问题。
   HyDE：领域词与措辞差距大。
   step-back：用户问细节但 KB 是概览。
   decomposition：多跳 QA。
   *对应：L09*

**Q15. "Lost in the middle" 是什么？怎么解决？**
A. Liu et al. 2023：LLM 倾向关注首尾。
   解决：相关最高的放首尾，相关低的放中间；或用 attention 增强。
   *对应：L10*

### 生成

**Q16. 什么是 grounded prompting？关键要素？**
A. 系统 prompt 明确"仅基于证据"；JSON-mode 响应；引用校验；abstain 通道。
   *对应：L11*

**Q17. 引用幻觉如何检测？**
A. 解析 LLM 返回的 citations 列表，与 valid_evidence_ids 取交集。
   空交集 → 强制 abstained。
   *对应：L11 / P1 postmortem*

**Q18. Faithfulness 与 Answer Relevancy 区别？**
A. Faithfulness：答案是否基于 context（事实一致性）。
   Answer Relevancy：答案是否切题（用户角度）。
   *对应：eval/ragas*

**Q19. 拒答（abstain）的阈值如何设？**
A. 经验：若 top-1 分数 < 0.3 触发拒答；或 LLM 自评置信度。
   太低：幻觉多；太高：用户体验差。
   *对应：L11 / P1*

**Q20. 什么是 Self-RAG / CRAG / Agentic RAG？**
A. Self-RAG：LLM 自评 + reflection tokens。
   CRAG：Confidence 触发 Correct/Incorrect/Ambiguous 三动作。
   Agentic：多步 tool use + 反思。
   *对应：L12*

**Q21. GraphRAG 是什么？何时用？**
A. 基于文档引用图（如 wikilinks）建图；多跳 QA 用。
   适合：知识库文档互引强（Obsidian vault）。
   不适合：无引用、简单事实查询。
   *对应：L13*

**Q22. 多跳问答的难点？**
A. 单独问题各自答不出，组合才行。GraphRAG / decompose / sub-QA 是常见解。
   *对应：L09 / L13*

**Q23. JSON-mode vs free-form 哪个好？**
A. JSON-mode：易校验、可程序化处理、引用追踪。
   free-form：自然但难校验。
   生产首选 JSON-mode + 解析失败 fallback。
   *对应：L11*

**Q24. token 计数为什么重要？**
A. 控制成本 + 防止超出 LLM 上下文窗口。
   tiktoken 精确，但 unicode 中文按字节算。
   *对应：L04 进阶*

**Q25. 召回 (recall) 与精度 (precision) 的取舍？**
A. 召回优先：宁多不少，rerank 兜底（RAG 通常）。
   精度优先：少而精，user UX（搜索通常）。
   选哪个看下游：rerank 强则召回优先；无 rerank 则精度优先。
   *对应：L05 / L08*

---

## Ⅱ. Production（25 题）

### 架构

**Q26. RAG 系统的典型组件？**
A. Loader → Chunker → Embedder → Index → Retriever → Reranker → Context Packer → LLM → Validator → UI。
   *对应：P1 架构图*

**Q27. 增量索引与全量重建如何权衡？**
A. 增量：节省时间，但 manifest 复杂（需跟踪每个文件 hash）。
   全量：稳定但慢（每晚跑）。
   kbchat 选 manifest + change_plan：版本漂移触发全量。
   *对应：kbchat.indexing.manifest*

**Q28. 100 万文档的检索架构？**
A. 粗排：HNSW (top-100) → 精排：cross-encoder (top-5)。
   或者：分片 + 多 embedding 模型 ensemble。
   *对应：L08 进阶*

**Q29. 流式输出如何实现？**
A. LLM SDK 支持 `.stream()` 或 SSE；Web 层 FastAPI StreamingResponse / Server-Sent Events。
   注意：生成完成才能做引用校验，so 流式期间不显示引用。
   *对应：L11 / P1*

**Q30. 异步 vs 同步？何时用谁？**
A. 同步：单 query 链路，简单。
   异步：批量 / 高并发 / I/O 密集（llm call、向量检索）。
   kbchat 现在同步，L22 异步化。
   *对应：L12 / 课程 L22*

### 性能 / 成本

**Q31. p50 / p95 延迟分别代表什么？怎么监控？**
A. p50 = 中位数；p95 = 95% 请求的延迟。
   OpenTelemetry / LangSmith 监控；告警阈值：p95 > 2s。
   *对应：L14*

**Q32. 一次 query 的成本构成？**
A. Embedding（本地 0）+ Retrieval（本地 0）+ LLM call（云 API 按 token）。
   一般 LLM 占 95%+。压缩 context / 选小模型 / 缓存。
   *对应：L10 / L14*

**Q33. Embedding 缓存如何实现？**
A. 用文件 path + mtime + 内容 hash 作 key，存 dict / Redis。
   内容不变 → 直接复用。
   *对应：L02 进阶*

**Q34. LLM 调用如何限流？**
A. 令牌桶（Token Bucket）按 QPS；按用户配额；MMX_MAX_CALLS_PER_QUERY 控单次 query。
   *对应：kbchat.config / P1 config*

**Q35. 为什么 trace 不能含 chunk 文本？**
A. 隐私 + 合规（PII 可能泄漏）。
   kbchat 只记 stage / duration / doc_id 数量。
   *对应：L14*

### 安全

**Q36. RAG 系统的攻击面？**
A. 1. prompt injection（用户问题中含"忽略以上指令"）
   2. 路径穿越（../）
   3. URI 注入（obsidian://?path=evil）
   4. 分类排除绕过
   5. 嵌入反演（embedding inversion attack）
   *对应：L15*

**Q37. 怎么防御 prompt injection？**
A. 无 100% 防御；纵深防御：regex 关键词 + LLM 二次确认 + 输出端引用校验。
   *对应：L15 / P1 postmortem*

**Q38. ACL 怎么设计？**
A. Single entry point：`is_allowed(path)`。
   文档级敏感度 + 用户角色 + 索引阶段过滤（不是检索时）。
   *对应：L15 / P1 ADR-0003*

**Q39. 敏感文档泄露如何检测？**
A. 自动化测试：100 个 restricted 文档，用 intern 角色问 → 必须 abstained。
   集成到 CI。
   *对应：P1 tests/test_acl.py*

**Q40. obsidian:// URI 注入怎么防？**
A. `urllib.parse.quote()` 编码；拒绝含 `;/?:@&=+$,#` 的路径；resolve 后必须落在 vault 内。
   *对应：L15*

### 评测

**Q41. 离线评测怎么搭？**
A. 50–200 题金标；7 个 slice 覆盖；每次 PR 跑；阈值不达标阻塞合并。
   *对应：P1 / eval/regression*

**Q42. RAGAS 是什么？4 个核心指标？**
A. 开源 RAG 评测库。
   Faithfulness / Answer Relevancy / Context Precision / Context Recall。
   *对应：eval/ragas*

**Q43. 在线 A/B 怎么设计？**
A. 控制组 vs 实验组；metric：用户满意度 + 任务完成率 + 引用点击率 + 拒答率。
   需 1000+ 样本才显著。
   *对应：L14 进阶*

**Q44. 如何判断 hallucination？**
A. 1. citation_validity（引用必须落到真实 chunk）
   2. faithfulness（句子能否在 context 中找到）
   3. LLM-as-judge（GPT-4 打分）
   *对应：L11 / eval/ragas*

**Q45. 评测集怎么维护？**
A. 标注人员 + 复审；线上 badcase 反哺金标；版本化（golden_v1, v2）。
   *对应：datasets/golden*

### 部署

**Q46. Streamlit vs FastAPI + React 何时用谁？**
A. Streamlit：内部工具、demo、1 周出活。
   FastAPI + React：生产 SaaS、需复杂交互。
   *对应：L16*

**Q47. 缓存怎么分层？**
A. 1. Embedding 缓存（按内容 hash）
   2. Retrieval 缓存（按 query hash + 索引版本）
   3. LLM 响应缓存（按 query + context hash）
   4. CDN 缓存（UI 资源）
   *对应：L14 进阶*

**Q48. Chroma 持久化怎么做？**
A. PersistentClient(path=...)；增量 add/update；不存全量在内存。
   Lock 处理：单进程访问。
   *对应：kbchat.indexing.chroma_store*

**Q49. 部署到 K8s 的注意事项？**
A. 1. 镜像大小（sentence-transformers 1.5GB）
   2. GPU 资源（cross-encoder）
   3. ConfigMap 注入配置
   4. Liveness/Readiness 探针
   5. 水平扩展 + 共享 Chroma 实例
   *对应：课程 L23*

**Q50. 一次事故复盘（postmortem）的关键要素？**
A. 1. 时间线（what happened）
   2. 影响范围（users affected）
   3. 根因（root cause，不是症状）
   4. 修复（mitigation + 长期）
   5. 行动项（action items + owner + due）
   *对应：P1 postmortem*

---

## 配套材料

- **STAR 故事** → [`star-stories.md`](./star-stories.md)
- **1 页简历** → [`resume.md`](./resume.md)
- **Mock 脚本** → [`mock-script.md`](./mock-script.md)
