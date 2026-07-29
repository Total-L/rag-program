# P6 — 企业级摄取平台（Enterprise Ingestion Platform）

> 回答四个问题的**可运行**答案：怎么提取企业级数据 / 怎么优化数据 / 怎么把数据写进向量数据库 / 整个流程长什么样。

本项目补齐的是 RAG 里最被低估、也最容易在面试里被问穿的一层：**数据工程**。
仓库原有内容在检索算法（混合检索、重排、Agentic RAG、评测门）上已经很扎实，
但摄取侧此前只有 `glob("*.md")` + 立即读全文，没有连接器、没有增量、没有脱敏、
向量库也只会 append。P6 把这一层补成生产形态。

---

## 一条命令看全流程

```bash
cd projects/p6-ingestion-platform

python run_ingest.py --full-scan        # 全量摄取（允许判定删除）
python run_ingest.py                    # 增量摄取（走游标）
python run_ingest.py --stats            # 只看当前状态
python run_ingest.py --embedder hash    # 无 Ollama 时验证流程（无语义能力）
```

流水线（每一步都可单独替换）：

```
连接器 list（只取元数据，不下载正文）
  → manifest diff（算出真正要干的活：new / changed / unchanged / deleted）
  → fetch 正文（只对 new + changed）
  → 解析（复用 P5 的 PDF/DOCX/XLSX/HTML loaders + PaddleOCR）
  → 归一化 → 学语料级样板 → 去样板 → 质量过滤 → 近重复检测
  → 敏感度自动分级 → PII / 密钥脱敏          ← 顺序不能换，见下文
  → 分块（表格感知 / 标题感知）+ 上下文前缀 + 元数据增强
  → 批量嵌入（Ollama，带重试与缓存）
  → 向量库 upsert + reconcile（清僵尸向量）
  → mark_indexed（下一轮才能判 unchanged）
  → 源侧删除 → delete_by_source + forget
```

---

## ① 怎么提取企业级数据

四个连接器，统一产出 `SourceRecord` 信封（`src/models.py`），下游完全不感知来源。

| 连接器 | 企业对应 | 本机替身 | 换生产 |
|---|---|---|---|
| `fs` | 共享盘 / NAS | 本地目录（**真实可跑**） | 换挂载路径 |
| `sql` | Postgres / MySQL 业务库 | SQLite（**真实 SQL 可跑**） | 只换 DSN，SQL 不变 |
| `objectstore` | S3 / GCS / Azure Blob | 本地目录模拟对象列举 | 传 `endpoint_url` 指向 MinIO/S3 |
| `confluence` | Confluence / SharePoint | 录制 JSON fixture 回放 | 传 `base_url` + `token` |

**两阶段协议**是这层的核心设计：

```python
list_records()  # 只返回元数据（etag / mtime / author / acl）—— 便宜，可全量扫
fetch_body(rec)  # 只对 new/changed 下载正文 —— 贵，靠 manifest 把量压到最小
```

把两步合成一步（"列举即下载"）就等于每次增量都做全量下载 —— 这正是原 `_load_corpus`
的做法，文档量一上来就跑不动。

**provenance 必须在摄取那一刻捕获**（事后补不回来）：`source_id / uri / author /
mtime / etag / acl_principals / sensitivity / version / content_sha256`。

### 增量 / CDC（`src/manifest.py`）

`change_key = etag or sha256`：**优先用源侧版本标识**，因为拿 etag 不需要下载正文。

三档增量能力，按源库能力从好到差：
1. **CDC / 逻辑复制**（Debezium）：最实时，要 DBA 配合
2. **watermark 列**（本实现）：`WHERE updated_at >= :cursor`，覆盖 95% 场景
3. **全表 + 内容哈希**：兜底，贵

已知坑（写在代码注释里而不是藏着）：watermark 用 `>=` 而不是 `>` —— 同一时间戳多行时
用 `>` 会漏数据，宁可多读一条由 `change_key` 去重。

**删除判定是分场景的**：
- `full_scan=True`：本轮没见到的判为已删除
- `full_scan=False`：**不做删除判定**（增量模式"没扫到"≠"被删除"）
- SaaS 连接器 `supports_full_scan()` 返回 `False` —— API 限流/权限差异会让列举不完整，
  据此删数据就是误删事故

---

## ② 怎么优化数据

### 归一化（`src/clean.py`）

NFKC + 零宽字符 + CRLF + 空白折叠。**这是增量索引能生效的前提**：
全角"，"和半角","会算出不同的 sha256，于是每轮都误判"内容变了"，白白重嵌 + 制造僵尸向量。

### 去样板（两趟）

企业文档高度模板化。`find_repeated_lines()` 先在语料上统计"出现在 ≥50% 文档里的短行"，
再逐篇 `strip_boilerplate()`。

> **实测数据**：不先去样板，"年假政策"和"发票申请"的 MinHash 相似度是 **0.516**
> （因为共享 `## 适用对象 / - 全员 / ## 常见问题` 等模板段落），
> 主题完全不同却会被当成重复丢掉。去样板后降到 **0.281**。

还会清理**孤儿标题**：样板答案被删后独自留下的 `### 谁负责 X?` 零信息量却稀释嵌入语义。

### 近重复（手写 MinHash + LSH）

刻意不引 `datasketch`：本仓库的原则是"不只学框架，要会手写最小版本"。
LSH banding 把两两比较从 O(N²) 降到近似 O(N)。

> **阈值必须按文本长度校准**（实测踩过的坑）：k=5 字 shingle 下，一处 2 字改动
> 在长文档里相似度仍 ≈0.97，但在 30–40 字短句上只有 **≈0.66**。
> 用 0.92 抓短句近重复一定会漏 —— 这不是 bug，是 MinHash 的固有特性。

`shingles()` 返回 **set**，所以"同一句重复 N 次"的集合 ≈ 出现 1 次。
对样板多的文档是好事，但也意味着**不能靠复制文本来构造"长文档"**做相似度实验。

### PII / 密钥脱敏（`src/pii.py`）

审计判定为 **critical** 的缺口 —— 原来完全依赖手写 `sensitivity:` 标签。

9 类检测：中国手机号 / 身份证 / 银行卡 / 邮箱 / IPv4 / JWT / API key / 数据库 DSN / 私钥块。

三道防线：
- `redact_for_index()` —— 写库前：**密钥类彻底 DROP**，个人信息 MASK（保留尾号）
- `redact_for_trace()` —— 写日志前：一律 HASH + 截断（L14 声明了这条规则却没实现）
- `classify_sensitivity()` —— 按命中 PII 的严重度**自动升级**敏感度，**只升不降**

> ⚠️ 边界声明：正则覆盖结构化 PII，**覆盖不了**自由文本里的姓名/地址/病情。
> 真上生产要接 Presidio / NER。本模块不假装解决了全部问题。

**顺序敏感**：`classify_sensitivity` 必须在脱敏**之前**跑。
一旦脱敏，身份证已被遮蔽，分级就检不出来，文档会被错判成 public。
（这是开发中实际踩到并修掉的 bug，有回归测试守着。）

### 分块（`src/chunkers.py`）

填补 `p5/src/chunkers/` 空目录承诺过但没实现的能力：

| 策略 | 解决什么 | 代价 |
|---|---|---|
| `layout_chunks` | 维护完整标题路径 —— "该比例不得超过 15%" 不再无主语 | 免费 |
| `table_aware_chunks` | 表格分片时**表头随行**，否则 "800/晚" 失去语义 | 免费 |
| `parent_child_chunks` | 子块精准召回 + 父块提供上下文 | 存储翻倍 |
| `contextual_prefix` | 每块加一句"它在讲什么"（Anthropic contextual retrieval） | 默认免费启发式；可传 `llm_fn` |

`contextual_prefix(llm_fn=...)` 失败会静默退回启发式 —— LLM 挂了不能拖垮整条摄取。

> 注意：上下文前缀参与 `chunk_id` 计算（它会进嵌入文本），
> 所以改前缀策略需要一次全量重建。

---

## ③ 怎么把数据写进向量数据库

`src/store.py`：统一 `IndexStore` 接口，双后端。

| | Chroma | pgvector |
|---|---|---|
| 定位 | 本地 / 原型 | **声明的生产后端** |
| 起步 | 零基建 | 需要 Postgres |
| 写语义 | `upsert()` | `INSERT ... ON CONFLICT DO UPDATE` |
| 过滤 | `where={}`，仅标量 | 任意 SQL WHERE，**ACL 下推到库** |
| 与业务表 | 分离 | 同库可 JOIN、有事务 |

五个方法就是全部业务面：

```python
upsert(records)  # 幂等：重跑行数不变
delete_by_source(source_id)  # 源侧删除
reconcile_source(source_id, current_ids)  # ★ 清僵尸向量
query(embedding, k, where)  # 检索 + 元数据过滤
existing_chunk_ids(source_id)  # 算"真正需要重嵌的"
```

**`reconcile_source` 是关键**。`chunk_id` 含正文哈希，所以文档改一个字就是**新 id**，
光靠 upsert 删不掉旧的那条。不对账就会留下永久僵尸向量，静默污染召回 ——
这正是 P5 原来的 bug（`collection.add()` + skip-if-exists）。

pgvector 侧的 SQL 全部由**纯函数**生成（`pg_ddl / pg_upsert_sql / pg_reconcile_sql /
pg_query_sql`），所以没有真数据库也能单测 SQL 正确性，包括 HNSW 索引参数
（`m=16, ef_construction=64`）和 ACL 过滤下推。

**后端选择绝不静默降级**：`open_index_store("auto")` 在没有 `P6_PG_DSN` 时会
**大声打印警告**告诉你"这不是生产写路径"；`backend="pgvector"` 连不上则直接抛错。

---

## 本机验证状态（诚实标注）

| 项 | 状态 |
|---|---|
| 107 单测 + 端到端测试 | ✅ 全过（`pytest tests -q`） |
| fs / sql / objectstore / confluence 连接器 | ✅ 真实跑通（150 企业 fixture + SQLite + 本地对象 + fixture 回放） |
| Chroma 写路径（upsert / reconcile / delete / 过滤） | ✅ 实测 |
| Ollama 真实嵌入（`nomic-embed-text`, dim=768） | ✅ 实测 |
| 幂等（重跑 0 重嵌 0 写入） | ✅ 实测：153 unchanged，4.1s → 0.0s |
| **pgvector 端到端** | ❌ **未验证** —— 本机无 Docker / 无 Postgres。SQL 生成有单测，真库执行没跑过。 |
| S3 / MinIO 真实连接 | ❌ 未验证（走 local 后端） |
| 真实 Confluence API | ❌ 未验证（走 fixture 回放） |

要跑 pgvector：

```bash
docker compose -f ../../05-data-engineering/docker-compose.yml up -d postgres
export P6_PG_DSN='postgresql://rag:rag@localhost:5432/ragkb'
python run_ingest.py --backend pgvector --full-scan
pytest tests --run-pg          # 解锁 pgvector 端到端测试
```

---

## 关于测试语料的一个发现

跑全量摄取时会看到 **153 篇里有 118 篇被判为近重复**。这不是 bug：

`datasets/generators/enterprise.py` 用模板生成 150 篇文档，但**只有约 32 个不同主题**
（`finance-027` 和 `finance-010` 去掉 frontmatter 后**逐字节相同**）。
去掉 frontmatter 和模板样板后，正文平均只剩 ~129 字符，重复度极高 ——
即使把阈值提到 0.99，仍然是 118 篇。

含义：**ROADMAP 里"合成 200 份企业内文档"的多样性被高估了**。
近重复检测正确地暴露了这一点。要做有意义的检索评测，语料需要更高的真实多样性。

---

## 目录

```
projects/p6-ingestion-platform/
├── README.md
├── run_ingest.py              # 端到端入口（幂等，可被任意调度器调用）
├── fixtures/
│   └── confluence_pages.json  # Confluence API 录制回放
├── src/
│   ├── models.py              # SourceRecord / Chunk / IngestFailure
│   ├── manifest.py            # 增量 / CDC / 游标（SQLite）
│   ├── connectors/            # fs / sql / objectstore / confluence
│   ├── clean.py               # 归一化 / 去样板 / 语言 / 质量 / MinHash 近重复
│   ├── pii.py                 # 9 类 PII 检测 + 三道脱敏防线 + 自动分级
│   ├── chunkers.py            # layout / table / parent-child / contextual
│   ├── embeddings.py          # Ollama 批量+重试+缓存 / HashEmbedder(测试用)
│   └── store.py               # IndexStore：pgvector + Chroma 双后端
├── tests/                     # 107 passed, 1 skipped(pgvector)
└── data/                      # 运行产物（manifest.db / index / dlq / reports）
```

## 不在本轮范围（记为后续"生产运维"阶段）

编排（Airflow/Prefect DAG）、容器化部署与 IaC、密钥管理与环境晋升、
GDPR/保留/DSR、漂移检测、成本账本、告警、零停机蓝绿重建。

`run_ingest.py` 刻意做成**幂等入口**，所以上述任何调度器直接调用即可，
不需要为了接编排而改造它。
