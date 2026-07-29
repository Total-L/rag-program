#!/usr/bin/env bash
# rag-program — 企业程序本体安装脚本
# 不依赖 rag-course 存在；只装工作区自身。
# 共享 venv 默认 = 工作区下的 .venv（可被 RAG_PROG_VENV 覆盖）

set -e
cd "$(dirname "$0")/.."

VENV="${RAG_PROG_VENV:-$PWD/.venv}"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"

echo "════════════════════════════════════════════"
echo "  rag-program — 企业程序安装"
echo "════════════════════════════════════════════"
echo "  venv: $VENV"
echo "  不依赖 rag-course / kbchat"
echo ""

# 1. 创建 / 激活 venv
if [ ! -d "$VENV" ]; then
  echo "→ 创建 venv: $VENV"
  $PY -m venv "$VENV"
fi

# 2. 升级 pip
echo "→ 升级 pip..."
$PIP install --upgrade pip wheel setuptools >/dev/null

# 3. 安装本工作区依赖（不触发任何 kbchat 依赖）
echo "→ 安装 rag-program 依赖..."
$PIP install -e .

# 4. 拉 Ollama 模型（Ollama 已在跑时）
if command -v ollama >/dev/null 2>&1 && curl -fsS http://localhost:11434/api/tags --max-time 2 >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | grep -q "nomic-embed-text"; then
    echo "→ 拉取 nomic-embed-text ..."
    ollama pull nomic-embed-text || true
  fi
  if ! ollama list 2>/dev/null | grep -q "llama3.1"; then
    echo "→ 拉取 llama3.1:8b ..."
    ollama pull llama3.1:8b || true
  fi
fi

# 5. 验证
echo ""
echo "→ 验证安装..."
bash scripts/env_check.sh

echo ""
echo "✓ 安装完成"
echo ""
echo "下一步："
echo "  make verify-enterprise          # 跑全部企业程序测试"
echo "  bash scripts/install-course-mirror.sh   # 可选：装 course-mirror 附加层"
