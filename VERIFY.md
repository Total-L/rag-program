# 验证报告

> `scripts/self_test.py` 实跑结果。日期 2026/07/27。
> 解耦后两层结构：
> - **企业程序本体**（P1–P6 + Stage-5 + eval + interview）—— 不依赖 rag-course
> - **course-mirror 附加层**（16 个 lab）—— 需要 `pip install -e ".[course-mirror]"`

## 速查：怎么跑

```bash
# 1. 装企业程序本体
bash scripts/install.sh
make verify-enterprise        # 跑 P1–P6 + Stage-5 + eval

# 2. （可选）装 course-mirror 附加层
bash scripts/install-course-mirror.sh
make verify-course-mirror     # 跑 16 个 lab

# 3. 端到端
make verify-all
```

## 解耦审计

| 静态扫描项 | 期望 | 实际 |
|---|---|---|
| `projects/` 下 `from kbchat` / `import kbchat` 数 | 0 | **0** ✅ |
| `projects/` 下 `/Users/totallai/rag-course` 硬编码数 | 0 | **0** ✅ |
| `datasets/` 下 `/Users/totallai/rag-course` 硬编码数 | 0 | **0** ✅ |
| `pyproject.toml` 里隐式 kbchat editable 依赖 | 0 | **0**（改为 optional extra） ✅ |
| `scripts/install.sh` 强制 `pip install -e /rag-course` | 0 | **0** ✅ |
| `Makefile` 默认 `VENV=/rag-course-venv` | 0 | **0**（默认 `.venv`） ✅ |
| `course-mirror/labs/` 下 kbchat import | 不限量 | 16 个 lab 各 1–3 个 ✅（故意保留） |
| `test_no_kbchat.py` 守护 P1 边界 | pass | **pass** ✅ |

## 单元测试矩阵

| 套件 | 用例 | 状态 |
|---|---|---|
| P1（核心旗舰，含 9 个 index_store + 1 个 no_kbchat） | 20 | ✅ |
| P6（摄取平台） | 110 passed, 1 skipped | ✅ |
| P5 chroma_store | 9 | ✅ |
| Stage-5 教学 lab (L17–L22) | 6 | ✅ |
| course-mirror labs (L01–L16) | 14/16 跑（kbchat 装时），L04/L08 跳 | ✅ |

## P1 自实现覆盖（ADR-0005）

| 原 kbchat 接口 | 自实现位置 | 行数 |
|---|---|---|
| `kbchat.chunking.MarkdownHeadingSplitter` | `_compat.MarkdownHeadingSplitter` | ~50 |
| `kbchat.embeddings.embed_passages / embed_query` | `_compat.ollama_embed_passages` | ~20 |
| `kbchat.retrieval.context.pack_evidence` | `_compat.pack_evidence` | ~30 |
| `kbchat.models.Candidate` | `_compat.Candidate`（@dataclass） | ~10 |
| `kbchat.security.is_allowed` | `_compat.is_path_allowed` | ~20 |
| `kbchat.config.ConfigView` | 删（未使用） | 0 |

集中 120 行替代 kbchat 9 个子模块在 P1 的使用面。

## 已知问题（已记录在 QUICKSTART.md）

1. **L04 / L08 跳过**：transformers / sentence-transformers 与 pip 上 `decoders` 包名冲突
2. **小模型过度拒答**：llama3.2:1b 总是返回"我无法..." —— 换 llama3.1:8b 或 qwen2.5:7b
3. **mmx Auth 需用户登录**

## 怎么再跑一次

```bash
# 企业程序本体（一键）
make verify-enterprise

# 单独跑 P1 50 题金标
./.venv/bin/python projects/p1-enterprise-kb/tests/run_eval.py

# 单独跑 Stage-5 lab
make lab-L17   # / lab-L18 / ... / lab-L22

# 单独跑 P6 摄取端到端
make verify-p6

# 训练营配套 lab（需先 install-course-mirror）
make lab-L01   # / lab-L02 / ... / lab-L16
```
