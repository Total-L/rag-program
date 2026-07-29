# ADR 0005 — P1 自包含：不依赖 rag-course/kbchat

## 状态
已采纳（2025-XX-XX）

## 背景

`rag-program` 原本定位是「与 rag-course 训练营配套的实战工作区」。这导致 P1 等旗舰项目直接 `from kbchat...`：

- `kbchat.chunking.MarkdownHeadingSplitter` — 文档切分
- `kbchat.embeddings.embed_passages / embed_query` — BGE 嵌入（仅 `RAG_PROG_EMBED_BACKEND=kbchat-bge` 启用）
- `kbchat.retrieval.context.pack_evidence` — token budget 装箱
- `kbchat.models.Candidate` — 候选证据 dataclass
- `kbchat.security.is_allowed` — path ACL（仅 `KBCHAT_VAULT_PATH` 启用）
- `kbchat.config.ConfigView` — 配置（仅 try/import，未实际使用）

依赖关系是**强**的：
- `pip install -e /Users/totallai/rag-course` 把 kbchat 装到共享 venv
- `examples/compare_retrievers.py` 显式 `sys.path.insert(0, "/Users/totallai/rag-course")`
- `scripts/install.sh` 强制要求 rag-course 目录存在
- 任何对 kbchat 的升级会**无声**影响 P1 的运行

这让 `rag-program` 不再是「独立可运行的企业程序」。

## 决策

**P1 企业程序本体不再 import kbchat**。新增 `src/_compat.py` 集中自实现 4 处原 kbchat 接口（~120 行精简替身）：

| 原 kbchat 接口 | P1 自实现位置 |
|---|---|
| `kbchat.chunking.MarkdownHeadingSplitter` | `_compat.MarkdownHeadingSplitter`（基于 `#` 标题正则 + 段落切窗） |
| `kbchat.embeddings.embed_passages / embed_query` | `_compat.ollama_embed_passages / ollama_embed_query`（Ollama HTTP 客户端） |
| `kbchat.retrieval.context.pack_evidence` | `_compat.pack_evidence`（贪心装箱 + 粗略 token 计数） |
| `kbchat.models.Candidate` | `_compat.Candidate`（`@dataclass`） |
| `kbchat.security.is_allowed` | `_compat.is_path_allowed`（兜底拒绝 + `P1_VAULT_ALLOWLIST` 允许前缀） |
| `kbchat.config.ConfigView` | 直接删除（从未被使用） |

每处自实现都标注"原 kbchat 行为"对照表，方便读者回到训练营主线对比。

**同步改动**：
- `kbchat-bge` 备选支线被删（统一走 Ollama）
- `KBCHAT_VAULT_PATH` 环境变量改名为 `P1_VAULT_ALLOWLIST`（更明确"允许前缀"语义）
- 新增 `tests/test_no_kbchat.py`：CI 守护 `projects/p1-enterprise-kb/src/` 下零 `from kbchat` / `import kbchat` / `/Users/totallai/rag-course` 引用

## 后果

**正**：
- `pip install -e .` 在任何机器、任何 venv 都能跑 P1，不再要求 rag-course 存在
- P1 不被 kbchat 上游 API 漂移影响
- 真正实现「企业程序 + 训练营配套 lab」的双层结构
- 显式的 `_compat.py` 让 P1 自有工具的位置、行为都有文档

**负**：
- ~120 行自实现需要维护（chunking 边界、context pack 策略、token 估算）
- 自实现的 markdown heading 切分行为与 kbchat 不完全一致（用粗略 token 计数替代 tiktoken 不严格）
- path ACL 从 kbchat 的 vault path 校验简化为"允许前缀列表" —— 语义变窄，部署方需显式配置

**可逆性**：
- 自实现集中在 `_compat.py` 一个文件，~120 行；如未来要换回 kbchat，改 5 行 import 即可
- `test_no_kbchat.py` 守护边界；如要解除，删除测试 + 改 import 即可

## 不在范围

- `course-mirror/labs/` 下的 16 个 lab **故意保留**对 kbchat 的依赖（详见 `course-mirror/README.md`）
- P2/P3/P4/P5/P6 等项目：本来就不依赖 kbchat（已自包含）
- 训练营 lab 里的 kbchat 调用全部自实现化：超出范围，重复造轮子
