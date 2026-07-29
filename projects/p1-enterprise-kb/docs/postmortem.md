# P1 Postmortem — 已踩过的坑

## 坑 1：纯 dense 检索对专有名词召回差

**症状**：问"竞业限制补偿比例"，dense 检索返回 5 条全不相关。

**根因**：nomic-embed-text 在中文专有名词上 embedding 区分度不够。

**修复**：hybrid retrieval（BM25 + dense + RRF）。BM25 命中"竞业"+"补偿"，dense 兜底 paraphrase。

**指标**：召回从 0.40 提升到 0.75。

## 坑 2：引用幻觉（LLM 编造 [E99]）

**症状**：LLM 返回 `citations: ["E1", "E99"]`，但 E99 不在 evidence 中。

**根因**：LLM 在没有证据时倾向于"凑"引用编号。

**修复**：`validate_response` 严格校验 citations ⊆ valid_evidence_ids；不在则强制 abstained。

**指标**：citation_validity 从 0.85 提升到 1.00。

## 坑 3：abstained 通道被绕过

**症状**：用户问"创始人是谁？"，LLM 答"公司创始人可能是..."（瞎猜）。

**根因**：abstained 阈值未生效；系统 prompt 中"知识库找不到"不够强。

**修复**：
1. system prompt 加 `必须回答"我无法在..."，禁止使用"可能"等模糊词`
2. JSON-mode 强制 `abstained: true` 字段
3. 解析失败 → 默认 abstained

**指标**：abstention_correctness 从 0.60 提升到 0.85。

## 坑 4：ACL 漏网（restricted 文档被 intern 检索到）

**症状**：intern 角色检索返回带"工资"、"期权"的内容。

**根因**：早期版本只在 `ask()` 时过滤；BM25 索引里仍包含 restricted 文档。

**修复**：在 `index_corpus()` 阶段就按 role 过滤 → restricted 文档物理不在索引里。

**指标**：protected_path_leakage = 0。

## 坑 5：chunking 把代码块切断

**症状**：检索"VPN 接入地址"时返回的 chunk 在代码块中间断开。

**根因**：MarkdownHeadingSplitter 对长 code block 按字符切，未识别 fence。

**修复**：未来用 `langchain.text_splitter.MarkdownTextSplitter` 的 `protected_sections` 或自研增强。

**临时方案**：限制 code block 内的子切分粒度。

## 坑 6：换持久化 + 增量索引时，reconcile 把整篇文档都误删了

**症状**：第一次跑 `index_corpus()` 写入 1800 chunk；跑完发现 store.count() = **0**，self.index 也是空的。第二次跑同样 0，幂等性通过了，但库里啥都没有 —— 明明 upsert 写了却"消失"了。

**根因**：开发期按习惯写了 `collection.delete(where={"$and": [{"source_id": sid}, {"chunk_id": {"$nin": keep}}]})`。但 **Chroma 的 `where` 子句只查 metadata 字段，ID 是单独维度**（要走 `ids=` 参数）。`chunk_id` 既然在 `where` 里出现就什么也匹配不上，导致 `$and` 整体等价于"按 source_id 全删"——每篇文档的所有 chunk 被整个清空。

为什么 pgvector 那边没事：`DELETE WHERE source_id = X AND chunk_id NOT IN (...)` 是真表列，能正常过滤。两套后端语义不一致正是 IndexStore 这种抽象层的"陷阱"。

**修复**：
1. ChromaIndexStore.reconcile_source 改为先 `collection.get(where={"source_id": sid}, include=[])` 拿现有 ID，再 set 差集，最后 `collection.delete(ids=list(stale))`
2. P1IndexStore 在调用 reconcile_source 前用 `sorted({c["chunk_id"] for c in doc_chunks})` 去重 —— 同一 chunk_id 出现多次（例如同标题同正文）会让 set 与 list 语义不一致

**教训**：
- 双后端抽象时**不能假设两个后端的查询语义等价**——Chroma 的 where / pgvector 的 WHERE 是不同范式
- 跨后端测试必须用同一组 fixture 跑两边，单测只测一边会漏（pgvector 测过没问题但 Chroma 端是初次接入才暴露）
- 加 DEBUG print 时机很重要：第一次踩坑是因为只看了 upsert 后的 count，没看 reconcile 前的 existing → 误以为 reconcile 是无辜的

**指标**：修复后 store.count 在第一次/第二次/编辑后/重启后都保持稳定（1800 → 1800），增量正确（只改 1 篇 = 1 进 1 出），ACL 过滤正确（manager 1356 / intern 648）。
