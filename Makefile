# rag-program — 企业级 RAG 实战项目 Makefile
# 用法：make <target>。所有目标默认在 rag_program/ 下运行。
# 默认 venv：./.venv（可在环境变量 VENV=... 覆盖，兼容旧 venv 路径）
#
# 速查：
#   make install                  - 装企业程序本体（无 kbchat 依赖）
#   make install-course-mirror    - 装可选 course-mirror 附加层（需要 /rag-course）
#   make verify-enterprise        - 跑全部企业程序测试（P1-P6 + Stage-5 + eval）
#   make verify-course-mirror     - 跑 course-mirror/labs/ 全部 16 个 lab（需先 install-course-mirror）
#   make verify-all               = verify-enterprise + verify-course-mirror

VENV     ?= $(CURDIR)/.venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
SHELL    := /bin/zsh

DATA_DIR := $(CURDIR)/datasets/fixtures
REPORTS  := $(CURDIR)/eval/reports

# course-mirror 目录（训练营配套 lab，可选）
CM_DIR   := $(CURDIR)/course-mirror/labs

.DEFAULT_GOAL := help

.PHONY: help
help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# 0. 环境
# ──────────────────────────────────────────────
.PHONY: env-check
env-check:  ## 检查 Python / Ollama / mmx / 模型（不依赖 kbchat）
	@bash scripts/env_check.sh

.PHONY: install
install:  ## 安装企业程序本体（不依赖 rag-course）
	@bash scripts/install.sh

.PHONY: install-course-mirror
install-course-mirror:  ## 装可选 course-mirror 附加层（要求 /rag-course 存在）
	@bash scripts/install-course-mirror.sh

.PHONY: clean
clean:  ## 清理临时文件（不动 fixtures 与 reports）
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ipynb_checkpoints -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	@echo "✓ cleaned caches"

.PHONY: reset-data
reset-data:  ## 清理所有 fixtures / 索引 / trace（保留金标）
	rm -rf $(DATA_DIR)/* $(REPORTS)/*
	@echo "✓ data reset (golden kept)"

# ──────────────────────────────────────────────
# 1. course-mirror — 训练营配套 lab（可选，需装 kbchat）
# ──────────────────────────────────────────────
.PHONY: lab-L01 lab-L02 lab-L03 lab-L04 lab-L05 lab-L04-compare
lab-L01:  ## L01: NumPy 余弦检索 (course-mirror)
	$(PY) $(CM_DIR)/01-foundations/labs/L01-numpy-retrieval/run.py
lab-L02:  ## L02: Chunking 对比 (course-mirror)
	$(PY) $(CM_DIR)/01-foundations/labs/L02-chunking-compare/run.py
lab-L03:  ## L03: 最小 RAG (course-mirror)
	$(PY) $(CM_DIR)/01-foundations/labs/L03-minimal-rag/run.py
lab-L04:  ## L04: LangChain 复刻 (course-mirror)
	OLLAMA_LLM_MODEL=llama3.2:1b OLLAMA_EMBED_MODEL=nomic-embed-text \
		$(PY) $(CM_DIR)/01-foundations/labs/L04-langchain-version/run.py
lab-L05:  ## L05: 手工指标 (course-mirror)
	$(PY) $(CM_DIR)/01-foundations/labs/L05-manual-metrics/run.py
lab-L04-compare: lab-L04  ## L04 对照实验：L03 vs L04 跑 8 题 golden
	$(PY) $(CM_DIR)/01-foundations/labs/L03-minimal-rag/run_eval.py
	$(PY) $(CM_DIR)/01-foundations/labs/L04-langchain-version/eval.py

.PHONY: lab-L06 lab-L07 lab-L08 lab-L09 lab-L10
lab-L06:  ## L06: BM25 (course-mirror)
	$(PY) $(CM_DIR)/02-retrieval-quality/labs/L06-bm25/run.py
lab-L07:  ## L07: RRF 融合 (course-mirror)
	$(PY) $(CM_DIR)/02-retrieval-quality/labs/L07-rrf-fusion/run.py
lab-L08:  ## L08: 交叉编码器 (course-mirror)
	$(PY) $(CM_DIR)/02-retrieval-quality/labs/L08-cross-encoder/run.py
lab-L09:  ## L09: 查询改写 (course-mirror)
	$(PY) $(CM_DIR)/02-retrieval-quality/labs/L09-query-rewrite/run.py
lab-L10:  ## L10: Context packing (course-mirror)
	$(PY) $(CM_DIR)/02-retrieval-quality/labs/L10-context-packing/run.py

.PHONY: lab-L11 lab-L12 lab-L13 lab-L14 lab-L15 lab-L16
lab-L11:  ## L11: Grounded prompt (course-mirror)
	$(PY) $(CM_DIR)/03-advanced-production/labs/L11-grounded-prompt/run.py
lab-L12:  ## L12: Agentic RAG (course-mirror)
	$(PY) $(CM_DIR)/03-advanced-production/labs/L12-agentic-rag/run.py
lab-L13:  ## L13: GraphRAG (course-mirror)
	$(PY) $(CM_DIR)/03-advanced-production/labs/L13-graph-rag/run.py
lab-L14:  ## L14: Observability (course-mirror)
	$(PY) $(CM_DIR)/03-advanced-production/labs/L14-observability/run.py
lab-L15:  ## L15: Security (course-mirror)
	$(PY) -m pytest $(CM_DIR)/03-advanced-production/labs/L15-security/tests/ -v
lab-L16:  ## L16: Streamlit 烟雾测试 (course-mirror)
	$(PY) $(CM_DIR)/03-advanced-production/labs/L16-streamlit/smoke.py

.PHONY: verify-course-mirror
verify-course-mirror: lab-L01 lab-L02 lab-L03 lab-L04 lab-L05 lab-L04-compare lab-L06 lab-L07 lab-L08 lab-L09 lab-L10 lab-L11 lab-L12 lab-L13 lab-L14 lab-L15 lab-L16  ## course-mirror 全部 16 个 lab
	@echo "✓ course-mirror labs 全部通过"

# ──────────────────────────────────────────────
# 2. Stage 5 — 数据工程（L17-L22 + P6 摄取平台，企业程序本体）
# ──────────────────────────────────────────────
.PHONY: lab-L17 lab-L18 lab-L19 lab-L20 lab-L21 lab-L22
lab-L17:  ## L17: 企业数据源连接器
	$(PY) 05-data-engineering/labs/L17-connectors/run.py
lab-L18:  ## L18: 增量 / CDC
	$(PY) 05-data-engineering/labs/L18-incremental-cdc/run.py
lab-L19:  ## L19: 清洗 / 归一化 / 近重复
	$(PY) 05-data-engineering/labs/L19-clean-normalize/run.py
lab-L20:  ## L20: PII / 密钥脱敏
	$(PY) 05-data-engineering/labs/L20-pii-redaction/run.py
lab-L21:  ## L21: 高级分块
	$(PY) 05-data-engineering/labs/L21-advanced-chunking/run.py
lab-L22:  ## L22: pgvector 写路径
	$(PY) 05-data-engineering/labs/L22-pgvector-write/run.py

.PHONY: verify-data-engineering
verify-data-engineering: lab-L17 lab-L18 lab-L19 lab-L20 lab-L21 lab-L22  ## 阶段 5 教学 lab 全验证
	@echo "✓ Stage 5 教学 lab 全部通过"

.PHONY: verify-p6
verify-p6:  ## P6 摄取平台端到端 + 单测
	cd projects/p6-ingestion-platform && $(PY) -m pytest tests/ -q

.PHONY: verify-p6-pg
verify-p6-pg:  ## P6 pgvector 端到端（需先 docker compose up postgres）
	cd projects/p6-ingestion-platform && $(PY) -m pytest tests/ --run-pg -q

.PHONY: data-platform-up
data-platform-up:  ## 起本地基座（postgres + minio）
	docker compose -f 05-data-engineering/docker-compose.yml up -d

.PHONY: data-platform-down
data-platform-down:  ## 关本地基座
	docker compose -f 05-data-engineering/docker-compose.yml down

# ──────────────────────────────────────────────
# 3. 主项目 + 轻量项目（企业程序本体）
# ──────────────────────────────────────────────
.PHONY: verify-p1
verify-p1:  ## P1 旗舰：50 题金标 + 阈值
	$(PY) projects/p1-enterprise-kb/tests/run_eval.py

.PHONY: verify-p2 verify-p3 verify-p4 verify-p5
verify-p2:  ## P2 轻量：代码文档 RAG（用默认 sample_corpus）
	$(PY) projects/p2-code-docs-rag/src/smoke.py
verify-p3:  ## P3 轻量：客服工单
	$(PY) projects/p3-customer-service/src/smoke.py
verify-p4:  ## P4 轻量：金融研报
	$(PY) projects/p4-finance-research/src/smoke.py
verify-p5:  ## P5 轻量：多格式 loader
	$(PY) projects/p5-multi-format-loader/src/smoke.py

.PHONY: verify-p2-4
verify-p2-4: verify-p2 verify-p3 verify-p4 verify-p5  ## 4 个轻量项目验证

.PHONY: verify-enterprise
verify-enterprise: env-check verify-data-engineering verify-p6 verify-p1 verify-p2-4  ## 企业程序本体一键验证（不依赖 kbchat）
	@echo ""
	@echo "════════════════════════════════════════════"
	@echo "  ✓ verify-enterprise PASSED"
	@echo "════════════════════════════════════════════"

# ──────────────────────────────────────────────
# 4. 评测 / 面试
# ──────────────────────────────────────────────
.PHONY: eval-ragas
eval-ragas:  ## RAGAS 跑全部项目
	$(PY) eval/ragas/run.py --all

.PHONY: regression
regression:  ## CI 阈值检查
	$(PY) eval/regression/check_thresholds.py

.PHONY: mock-interview
mock-interview:  ## 50 题抽样自测
	$(PY) interview/mock_runner.py --sample 10

.PHONY: verify-all
verify-all: verify-enterprise verify-course-mirror mock-interview  ## 端到端全验证（先装 course-mirror 才能全绿）
	@echo ""
	@echo "════════════════════════════════════════════"
	@echo "  ✓ verify-all PASSED"
	@echo "════════════════════════════════════════════"
