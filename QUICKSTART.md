# 5 分钟上手 rag-program

> 给 RAG 零基础到面试的快速路径。读完 + 跑通 = 你能向面试官讲清 RAG 全链路。

## Step 1（30 秒）：理解 RAG

RAG = Retrieval-Augmented Generation。流程：

```
用户问题 → Embedding → 检索相关文档 → 拼成 context → LLM 基于 context 回答
```

vs 纯 LLM：知识固化、不能更新、容易幻觉。RAG 注入新知识 + 引用溯源。

## Step 2（1 分钟）：装 + 验证企业程序

```bash
cd /Users/totallai/rag_program
bash scripts/install.sh        # 装到 .venv（不依赖 rag-course）
make env-check                 # 验证 Ollama / Python 包
```

输出：✓ Python ✓ mmx ✓ Ollama ✓ 工作区结构

## Step 3（1 分钟）：跑通主项目 P1

```bash
# 1. 准备数据（已生成在 datasets/fixtures/）
ls datasets/fixtures/enterprise | head

# 2. 跑 smoke
./.venv/bin/python projects/p1-enterprise-kb/src/smoke.py
```

输出：
```
→ 索引 chunks: 1469
Q: 员工每年能休多少天年假？
A: 司龄 1 年以下 5 天 [1] (含 E1 引用)
```

**关键文件**：[`projects/p1-enterprise-kb/src/pipeline.py`](projects/p1-enterprise-kb/src/pipeline.py) — 完整 RAG pipeline，hybrid retrieval + 引用校验 + 拒答。
**自包含**：P1 内部 `src/_compat.py` 自实现 markdown heading 切分、Ollama 嵌入、context pack、path ACL（详见 [ADR-0005](projects/p1-enterprise-kb/docs/adr/0005-p1-kbchat-free.md)）。

## Step 4（1 分钟）：打开金标

```bash
# 50 题金标，覆盖 7 个 slice
head -5 datasets/golden/golden_v1.jsonl | python3 -m json.tool
```

7 个 slice：semantic / lexical / metadata / multihop / bilingual / unanswerable / adversarial。

## Step 5（1 分钟）：跑评测

```bash
./.venv/bin/python projects/p1-enterprise-kb/tests/run_eval.py
```

输出 `eval/reports/p1_golden.json`：
```json
{
  "overall": {
    "n": 50,
    "citation_validity": 0.96,
    "abstention_correctness": 0.84,
    "contains_expected": 0.62
  }
}
```

## （可选）Step 6：跑训练营配套 lab

```bash
bash scripts/install-course-mirror.sh   # 需要 /rag-course 存在
make verify-course-mirror              # 跑 16 个 lab
```

详见 [`course-mirror/README.md`](course-mirror/README.md)。

## 现在你能向面试官讲什么

学完 Step 1–5，你应该能用 5 分钟讲清：

1. **RAG 全链路**：query → embed → retrieve → pack → generate → validate
2. **为什么 hybrid retrieval**：BM25（精确词）+ dense（语义）+ RRF（融合）
3. **为什么需要引用校验**：LLM 会编造 [E99] 这样的不存在的引用
4. **为什么需要 abstention**：知识库没有时，胡编不如拒答
5. **生产关注什么**：p50/p95 延迟、错误率、ACL、prompt injection

## 下一步（每周节奏）

| 周 | 重点 | 跑什么 |
|---|---|---|
| 1 | 基础 | `make lab-L01..lab-L05`（course-mirror） |
| 2 | 检索 | `make lab-L06..lab-L10`（course-mirror） |
| 3 | 高级 | `make lab-L11..lab-L16`（course-mirror） |
| 4 | 面试 | `make mock-interview` 每周 1 次 |
| 持续 | 生产 | `make verify-enterprise` 包含 P1-P6 + Stage-5 + eval |

## 简历上的项目链接

- **P1 Enterprise KB**：https://github.com/yourname/rag-program/tree/main/projects/p1-enterprise-kb
- **P6 Ingestion Platform**：https://github.com/yourname/rag-program/tree/main/projects/p6-ingestion-platform
- **50 题库**：[interview/questions.md](interview/questions.md)
- **3 STAR 故事**：[interview/star-stories.md](interview/star-stories.md)
- **1 页简历**：[interview/resume.md](interview/resume.md)

## 故障排查

| 问题 | 解决 |
|---|---|
| `Ollama 404 Not Found` | 模型名错：`ollama list` 看实际名，常见 `llama3.1:8b` |
| `mmx Auth Failed` | `mmx auth login --api-key <key>` |
| `kbchat 未装` | 跑 `bash scripts/install-course-mirror.sh`（要 /rag-course 在） |
| `chromadb` 装不上 | `pip install chromadb>=1.5,<2.0` |

## 关键文档

- [`00-NAVIGATION.md`](00-NAVIGATION.md) — 工作区地图
- [`ROADMAP.md`](ROADMAP.md) — 4 阶段路线
- [`SUMMARY.md`](SUMMARY.md) — 交付总览
- [`VERIFY.md`](VERIFY.md) — 验证报告
- [`projects/p1-enterprise-kb/docs/adr/`](projects/p1-enterprise-kb/docs/adr/) — P1 架构决策（含 ADR-0005）
