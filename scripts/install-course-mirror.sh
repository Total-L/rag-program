#!/usr/bin/env bash
# rag-program — course-mirror 附加层安装脚本
# 需要 /Users/totallai/rag-course 存在；缺包时清晰报错。
# 跑完会启用 course-mirror/labs/ 下 16 个 lab。

set -e
cd "$(dirname "$0")/.."

VENV="${RAG_PROG_VENV:-$PWD/.venv}"
PIP="$VENV/bin/pip"

echo "════════════════════════════════════════════"
echo "  rag-program — course-mirror 附加层安装"
echo "════════════════════════════════════════════"
echo "  venv: $VENV"
echo ""

# 1. 先确保基础安装已跑
if [ ! -d "$VENV" ]; then
  echo "→ 基础 venv 不存在，先跑 install.sh..."
  bash scripts/install.sh
fi

# 2. 检查 rag-course 是否存在
RAG_COURSE_DIR="${RAG_COURSE_DIR:-/Users/totallai/rag-course}"
if [ ! -d "$RAG_COURSE_DIR" ]; then
  echo "✗ $RAG_COURSE_DIR 不存在"
  echo "  course-mirror 附加层需要 rag-course 训练营源码存在"
  echo "  请先 clone 或调整 RAG_COURSE_DIR 环境变量"
  exit 1
fi
echo "→ rag-course: $RAG_COURSE_DIR"

# 3. 装 kbchat 本地依赖（PEP 508 direct URL reference,不能进 PyPI metadata —
#    故 pyproject.toml 不再用 [course-mirror] extra,这里直接装 file:// 路径）
echo "→ 装 kbchat @ file://$RAG_COURSE_DIR..."
$PIP install "kbchat @ file://$RAG_COURSE_DIR"

# 4. 验证
echo ""
echo "→ 验证 course-mirror..."
if [ -d "course-mirror/labs/01-foundations" ]; then
  echo "  course-mirror 目录存在"
else
  echo "  ✗ course-mirror 目录缺失"
  exit 1
fi

# 测一个最小 import
$PY -c "import kbchat; print('  kbchat', kbchat.__file__)" || {
  echo "✗ kbchat import 失败"
  exit 1
}

echo ""
echo "✓ course-mirror 安装完成"
echo ""
echo "下一步："
echo "  make verify-course-mirror   # 跑 course-mirror/labs/ 全部 16 个 lab"
echo "  make verify-all             # 跑企业程序 + course-mirror + 面试"
