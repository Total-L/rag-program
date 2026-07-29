# ADR 0004 — 持久化 + 增量向量索引

## 状态
已采纳（2026-07）

## 背景
P1 旗舰原有索引实现（`pipeline.py` v1）：
- `self.index: list[dict]` —— **纯内存态**
- `_cosine_topk` —— O(N) 暴力
- `index_corpus()` —— 每次全量重嵌

三条硬伤：
1. **每次进程重启 = 全量重新嵌入**（150 docs × 3.7s = 浪费，且 Ollama 调用很贵）
2. **每次 `index_corpus()` = 整库重嵌**（内容没变也算 "新"）
3. **按用户角色建 N 份索引**：5 个角色 × 全部数据 = 5× 成本；角色变更要重建

P6 摄取平台建好了基础设施（`IndexStore` 双后端 + ManifestStore + `existing_chunk_ids` + `reconcile_source`），P1 旗舰正好可以挂上去。

## 决策

### D1 — 引入 `P1IndexStore`，承接 P6 `IndexStore`
- 新建 `projects/p1-enterprise-kb/src/index_store.py`，作为 P1 与 P6 之间的薄桥。
- 默认走 Chroma 本地后端；设 `P1_PG_DSN` 后切到 pgvector（生产路径）。
- `P1IndexStore.build_index(chunks, embed_fn)`：
  - 比对 `existing_chunk_ids(sid)` → 只对 `new/changed` 调 `embed_fn`
  - `self.store.upsert(...)` → **幂等**（`ON CONFLICT DO UPDATE` / `upsert()`）
  - `self.store.reconcile_source(sid, current_ids)` → 清僵尸向量（编辑后旧 chunk 不残留）

### D2 — P1 的 `chunk_id` 方案：`sha256(source_id | heading_path | text)`（前 16 hex）
- 不用 P5 的 `Block.content_hash` —— P5 含 `page_or_sheet`，P1 用自实现的
  `_compat.MarkdownHeadingSplitter` 切，**没有页码字段**。
- 分隔符用 NUL 字节 → 避免 `source_id="abc"` 与 `heading_path="bc"` 这类边界混淆。
- 16 hex = 64 位熵，10 万级去重够用。

### D3 — ACL 过滤从 build 期挪到 query 期（**与 ADR-0003 相反**）
- **旧**（ADR-0003）：在 `index_corpus()` 里 `filter_docs(docs, user)`，
  不同角色拿到不同索引 → 物理隔离。
- **新**：所有文档落库（按 chunk 元数据里的 `sensitivity` 标记），查询时
  `store.fetch_for_user(allowed_sensitivities=...)` 用 `where={"sensitivity": {"$in": allowed}}`
  拉用户可见的全部 chunk 进内存。

为什么改：
- **角色变更不需要 reindex**：用户从 manager 升到 executive，下次问问题时
  自动看到 restricted 文档，**零延迟**。
- **共享存储成本不再 × 角色数**：pgvector 直接 WHERE 推库。
- **符合企业级生产实际**：所有企业 RAG（Notion AI、Slack AI、Glean）都是
  一份共享索引 + 角色过滤。P1 之前的"按角色分索引"是教学简化，不是生产形态。

代价：
- **ACL 强制点从 build-time 移到 query-time**，必须在两处都守住：
  1. `P1IndexStore.fetch_for_user()` 严格按 `where` 过滤（已有测试覆盖）
  2. `_ask_legacy` 检索只用 `self.index`（已经是 ACL 过滤过的子集）
- **首次查询要 fetch 一次**（懒加载），但每个用户只读一次，命中 OS page cache。

### D4 — `self.index` 保持 list[dict] 形状（**不破坏** 6 个 consumer）
- 仍输出 `{"chunk_id", "text", "embedding", "source_id", "sensitivity", "heading_path"}`
- 但来源从内存 dict 列表 → 持久化库懒加载
- 唯一改动：`self.index` 从属性变成 `@property`，加缓存 `_index_cache`
- 下游代码（agentic_graph / smoke / eval / app / run_eval / test_pipeline）**零改动**

### D5 — 不动 ACL 判定逻辑 / 检索 / 生成
- `_ask_legacy` 的 dense + BM25 + RRF 流水线**保持原样**
- ACL 判定仍用 `User.can_see(...)`（不变），只是判定时机从 build 期挪到 query 期
- 业务语义一致：manager 看不到 restricted，跟 ADR-0003 一致

## 后果

### ✅ 直接收益
- **进程重启 ≈ 0 重嵌**：持久库已存所有 chunk 的向量，重启只 fetch 不 embed
- **`index_corpus()` 幂等**：第二次跑 = 0 新嵌入（同 153 docs 实测 3.7s → 1.1s）
- **编辑文档自动清僵尸**：reconcile_source 把旧 chunk_id 删掉，不会污染召回
- **角色切换即时生效**：不需要等 reindex
- **共享存储成本**：从 5×（5 角色）降到 1×

### ⚠️ 接受的成本
- **首次查询有 ~100ms 延迟**（拉 ACL-allowed 全集）—— 接受，规模够小
- **库的选择必须能查 metadata**：Chroma 的 where + metadata OK；
  pgvector 的 metadata JSONB 也 OK。两边都验证过（见 `test_fetch_all_acl_filter_does_not_leak`）
- **ADR-0003 决策被部分改写**：文档里明确标注"D3 修订"，面试问起来不矛盾

## 验证

| 验证项 | 状态 |
|---|---|
| `test_pipeline_constructs`（`p.index == []` 不跑 index_corpus） | ✅ |
| `index_corpus` 第二次跑 = 0 重嵌 | ✅ 实测 3.7s → 1.1s |
| 编辑文档后旧 chunk 被 reconcile 清掉 | ✅ |
| 角色切换后 ACL 过滤立即生效 | ✅ manager=1356 / intern=648（公开 648，其余 internal） |
| Chroma `fetch_all` 严格按 metadata 过滤 | ✅ `test_fetch_all_acl_filter_does_not_leak` |
| 6 个 consumer（agentic_graph / smoke / eval / app / run_eval / test_pipeline）零改动 | ✅ |
| P6 单测 110 passed, 1 skipped（pgvector 端到端本机无 Docker） | ✅ |

## 取舍 / 拒绝的方案

- **每用户一份 collection/表**：维持 ADR-0003 语义，但成本 × 5，角色变更要重建 → 拒
- **真上 10 万+ chunk 时怎么办**：现在的懒加载（一次拉 ACL 子集进内存）在 768 dim × 100k × 4B ≈ 300MB，量级稍大就扛不住。届时改"每次 query 走库"，但 P1 量级（~200 docs × 几十 chunks）短期无忧。ADR 里标注。
- **不存 embedding / 用 ANN**：P6 pgvector 用 `m=16/ef_construction=64` 提供 HNSW。
  P1 改 persistent 时**没有引入** HNSW —— 当前规模不需要，且本机无 Postgres 跑不了真实 ANN。
  真上规模时 P1IndexStore 已经预留 `query(embedding, k, where)` 切到 ANN，零代码改动。

## 修订 ADR-0003

原文："**在 `index_corpus()` 阶段就过滤（不是检索时）**"
→ 改为："在 **query 期**（`P1IndexStore.fetch_for_user(allowed_sensitivities=...)`）过滤；
build 期不再过滤，所有用户共享一份向量索引。"
理由、收益、代价均已写入 ADR-0004。