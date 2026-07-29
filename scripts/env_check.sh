#!/usr/bin/env bash
# rag-program — 企业程序环境检查脚本
# 验证 Python / mmx / Ollama / 模型 / 工作区结构；不再检查 kbchat（KBChat 是 course-mirror 附加层的依赖）

set -e
cd "$(dirname "$0")/.."

VENV="${RAG_PROG_VENV:-$PWD/.venv}"
PY="$VENV/bin/python"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { printf "${GREEN}✓${NC} %s\n" "$1"; }
fail() { printf "${RED}✗${NC} %s\n" "$1"; exit 1; }
warn() { printf "${YELLOW}!${NC} %s\n" "$1"; }

echo "════════════════════════════════════════════"
echo "  rag-program — 企业程序环境检查"
echo "════════════════════════════════════════════"

# 1. Python
if [ -x "$PY" ]; then
  ver=$("$PY" --version 2>&1)
  ok "Python: $ver"
else
  warn "Python not found at $VENV/bin/python（运行 scripts/install.sh 装）"
fi

# 2. mmx CLI
if command -v mmx >/dev/null 2>&1; then
  ver=$(mmx --version 2>&1 | head -1)
  ok "mmx CLI: $ver"
else
  warn "mmx CLI not on PATH（MiniMax 后端将不可用，Ollama 后端可继续）"
fi

# 3. mmx auth
if [ -f "$HOME/.mmx/config.json" ]; then
  ok "mmx auth config exists"
else
  warn "mmx auth config missing: $HOME/.mmx/config.json"
fi

# 4. Ollama
if curl -fsS http://localhost:11434/api/tags --max-time 2 >/dev/null 2>&1; then
  models=$(curl -fsS http://localhost:11434/api/tags --max-time 2 | "$PY" -c "import json,sys; d=json.load(sys.stdin); print(','.join(m['name'] for m in d.get('models',[])))" 2>/dev/null || echo "（解析失败）")
  ok "Ollama running: $models"
else
  warn "Ollama not running at http://localhost:11434（启动: ollama serve）"
fi

# 5. 关键 Python 包（企业程序本体依赖）
for pkg in numpy scikit-learn langchain langchain_community langgraph chromadb rank_bm25 tiktoken tree_sitter; do
  if "$PY" -c "import $pkg" 2>/dev/null; then
    ok "Python pkg: $pkg"
  else
    warn "Python pkg missing: $pkg"
  fi
done

# 6. 工作区结构
for p in datasets/fixtures datasets/golden eval/reports 05-data-engineering projects/p1-enterprise-kb projects/p6-ingestion-platform; do
  if [ -d "$p" ]; then
    ok "dir: $p"
  else
    mkdir -p "$p" 2>/dev/null || warn "dir missing & can't create: $p"
  fi
done

# 7. course-mirror 子模块（可选）
if [ -d "course-mirror/labs" ]; then
  ok "course-mirror 子模块存在（可选用，依赖 kbchat）"
  if "$PY" -c "import kbchat" 2>/dev/null; then
    ok "  kbchat importable（course-mirror 可跑）"
  else
    warn "  kbchat 未装 → course-mirror lab 不能跑（要跑请跑 scripts/install-course-mirror.sh）"
  fi
else
  warn "course-mirror 子模块不存在"
fi

# 8. 静态扫描：projects/ 下不应有 kbchat 引用（排除 test_no_kbchat.py 自身）
if grep -rn "from kbchat\|import kbchat" projects/ 2>/dev/null | grep -v "test_no_kbchat.py" | head -1 | grep -q .; then
  warn "projects/ 下发现 kbchat 引用（应已清理，详情见 ADR-0005）"
else
  ok "projects/ 下零 kbchat 引用"
fi

echo ""
echo "════════════════════════════════════════════"
echo "  环境检查完成"
echo "════════════════════════════════════════════"
