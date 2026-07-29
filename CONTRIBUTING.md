# 贡献指南

## 入门

1. **Fork** 本仓库 → 创建分支 → 提交 PR
2. PR 描述需说明：改了什么 / 为什么 / 如何验证
3. CI 全绿 + 一个 reviewer approve 才能 merge

## 开发环境

```bash
# Python 3.11+
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 跑测试（默认 58 用例，~5s）
python -m pytest projects/

# 跑回归 gate（需要 verify-p1 跑过的报告）
python eval/regression/check_thresholds.py
```

## 代码规范

- **Ruff** 已在 `pyproject.toml [tool.ruff]`，跑 `ruff check .`
- **MyPy** 已在 `[tool.mypy]`，跑 `mypy projects/p1-enterprise-kb/src/`
- Python ≥ 3.11，使用 `from __future__ import annotations`

## 项目结构

```
projects/p1-enterprise-kb/   # 企业知识库 RAG（旗舰项目）
projects/p2-code-docs-rag/   # 代码文档检索
projects/p3-customer-service/ # 客服助手
projects/p4-finance-research/ # 财报研究
projects/p5-multi-format-loader/ # 多格式 loader
projects/p6-ingestion-platform/  # 摄取平台
eval/                        # 评测与回归 gate
05-data-engineering/         # 数据工程三件套（docker-compose 等）
course-mirror/               # 训练营配套（PR 时不要混进来）
```

## 提交规范

- 原子提交：每个 commit 只做一件事
- Commit message 用祈使句："Add X" 而非 "Added X"
- 长描述放在 PR 里，commit message 简明

## CI / 回归门

PR 自动跑（GitHub Actions `.github/workflows/ci.yml`）：

| Job | 触发 | 内容 |
|---|---|---|
| `lint` | push + PR | `ruff check projects/ eval/ --output-format=github` + `ruff format --check` |
| `test` | push + PR | matrix py3.11/3.12/3.13 + `pytest projects/ -q -m "not integration"` |

回归门（`.github/workflows/regression.yml`）**默认不跑 PR**——真 RAGAS 需要 Ollama，跑 PR 会卡 GPU。触发方式：

- **手动**：GitHub UI → Actions → regression → Run workflow
- **定时**：每周一 02:00 UTC
- 需要 runner 装 Ollama + 拉 `llama3.2:3b`

```bash
# 本地跑 regression（需要本机装 Ollama）
make verify-p1
python eval/regression/check_thresholds.py
```

## 提 PR 前

- [ ] `python -m pytest projects/ -m "not integration"` 全过
- [ ] `python scripts/check_lockfile_drift.py` 0 退出（lockfile 与 pyproject 同步）
- [ ] `ruff check projects/ eval/` 无 error
- [ ] 改 `pyproject.toml` → 跑 `bash scripts/refresh_lockfile.sh`（见下）
- [ ] 改 `CHANGELOG.md` 加一条 entry（放在 `[Unreleased]` 下）
- [ ] 改 `eval/regression/thresholds.yaml` → 同步更新 `eval/regression/check_thresholds.py:THRESHOLDS`（双写，注意一致）

## Lockfile 同步（ENTERPRISE_AUDIT M3）

三个 lockfile 是部署的唯一可信源（用 `pip install --require-hashes -r`）：

| 文件 | 来源 | 谁用它 |
|---|---|---|
| `requirements.lock` | `requirements.in` (→ pyproject `[project]`) | 生产 Docker / CI runtime |
| `requirements-dev.lock` | `requirements-dev.in` (→ pyproject `[optional].dev`) | 本地开发 / CI lint+test |
| `requirements-otel.lock` | `requirements-otel.in` (→ pyproject `[optional].observability`) | 可选 OTel exporter 启用时 |

### 改 pyproject 后必须 refresh

```bash
# 一次性把三个 lockfile 都重新生成
bash scripts/refresh_lockfile.sh

# 或单独跑某个：
uv pip compile requirements.in --strip-extras --generate-hashes -o requirements.lock
```

为什么 hash 模式：`pip install --require-hashes` 拒绝未签名 hash 的包，
防供应链攻击（Snyk 2024 数据：12% PyPI 包被替换过一次以上）。

### drift 检测

CI 跑 `scripts/check_lockfile_drift.py`：
- pyproject `[project] dependencies` 必须全在 `requirements.in`
- 每个 `.lock` 必须包含对应 `.in` 的所有 top-level 包
- `requirements-otel.lock` 必须是独立栈（不能拉 fastapi/uvicorn）

如果本地改了 pyproject 但忘 refresh，CI 会 fail loud，
提示 `→ run scripts/refresh_lockfile.sh`。

### PR 清单

- 改 pyproject → 改 .in → 跑 refresh → 三个 lockfile 都进同一个 commit
- 不要手动编辑 .lock（hash 会和 uv/pip-tools 计算的不一致，drift 立刻触发）
- CI lint job 会 `pip install --require-hashes -r requirements.lock` 验证 installable

## 测试标注

- `@pytest.mark.slow` — 跑超过 10 秒的
- `@pytest.mark.integration` — 需要 Ollama / mmx 等外部依赖
- `@pytest.mark.security` — 安全相关
- `@pytest.mark.api` — FastAPI server
- `@pytest.mark.observability` — metrics / tracing

默认 `pytest projects/` 不跑 slow/integration；CI 才跑全套。

## Bug 报告

issue 模板：
1. 环境（OS / Python 版本 / 依赖版本）
2. 复现步骤
3. 期望 vs 实际
4. 报错日志（贴完整 traceback，不要截断）

## 安全问题

**不要**开 public issue。邮件给 maintainer（见 `MAINTAINERS.md`）。