# course-mirror — 训练营配套教学 lab

> 这是 `rag-program` 工作区里**唯一**保留对 `rag-course/kbchat` 依赖的子模块。
> 与企业程序本体（`projects/`、`05-data-engineering/`、`eval/`、`interview/`）**互不耦合**。

## 定位

`rag-course` 训练营的主线课程讲 RAG 算法与工程（混合检索、重排、Agentic、Observability 等）。
它的核心库是 `kbchat`，所有 lab 都 `import kbchat` 来验证概念。

本子目录里的 lab 是训练营主线的**对照实现**：

- 保留 `from kbchat...` 引用 —— 这是**故意为之**，目的是保持与训练营主线 lab 1:1 对齐，让读者可以来回对照。
- 不进入 `make verify-enterprise` 跑测链路。
- 不修改企业程序本体（`projects/*`、`05-data-engineering/*` 都不 import kbchat）。
- 跑测需要 `pip install -e ".[course-mirror]"`，即 `/Users/totallai/rag-course` 在本机存在。

## 目录

```
course-mirror/labs/
├── 01-foundations/         (L01-L05: NumPy/chunking/minimal RAG/LangChain/手工指标)
├── 02-retrieval-quality/   (L06-L10: BM25/RRF/cross-encoder/rewrite/context packing)
└── 03-advanced-production/ (L11-L16: grounded prompt/Agentic/Graph/observability/security/Streamlit)
```

## 安装

```bash
# 1) 装企业程序本体（必需）
pip install -e .

# 2) 装 course-mirror 附加层（可选）
pip install -e ".[course-mirror]"
# 该 extra 依赖声明在 pyproject.toml:
#   [project.optional-dependencies]
#   course-mirror = ["kbchat @ file:///Users/totallai/rag-course"]
# 缺包时 pip 会清晰报错，不会静默回退。
```

## 跑测

```bash
# 一次性跑全部 16 个 lab
make verify-course-mirror

# 或单独跑
cd course-mirror/labs/01-foundations/labs/L01-numpy-retrieval
python run.py
```

## 与企业程序本体的边界

| 项 | 企业程序 | course-mirror |
|---|---|---|
| import kbchat | 不允许 | 故意保留 |
| 在没有 rag-course 时能否跑 | ✅ | ❌（依赖 kbchat 安装） |
| 进入 `make verify-enterprise` | ✅ | ❌ |
| 进入 `make verify-all` | ✅ | ✅（前提是装了 course-mirror extra） |
| 与训练营主线 lab 对照 | 仅作"参考实现"基线 | 1:1 对照 |

## 为什么不让企业程序本体直接复用这些 lab？

`kbchat` 是教学包装库，接口稳定性依赖训练营节奏。企业程序追求：

1. **可独立运行**：clone 后 `pip install -e . && make verify-enterprise` 在任何机器、任何 venv 都能跑通。
2. **可独立演化**：不被 kbchat 上游 API 漂移影响。
3. **可独立测试**：不用同时维护两套 git 仓库的状态对齐。

代价是 P1 等旗舰项目需要自实现少量原 `kbchat` 提供的工具（chunking、context packing、path ACL）—— 但每一处自实现都有 ADR 记录取舍，量级在 100~200 行。

详见 `projects/p1-enterprise-kb/docs/adr/0005-p1-kbchat-free.md`。