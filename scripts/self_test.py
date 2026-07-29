"""端到端自检：跑企业程序 + course-mirror labs，输出 pass/fail 汇总。

跑：
    python scripts/self_test.py
    # 或
    RAG_PROG_VENV=/path/to/venv python scripts/self_test.py

说明：
- 企业程序项目（p1/p2/p3/p4/p5）默认跑；不依赖 kbchat。
- 16 个 lab 默认从 course-mirror/labs/ 找；若 kbchat 未装则跳过。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# venv 路径：默认 = 工作区 .venv；可被 RAG_PROG_VENV 覆盖
_DEFAULT_VENV = Path(__file__).resolve().parents[1] / ".venv"
VENV_PY = os.environ.get("RAG_PROG_VENV", str(_DEFAULT_VENV / "bin" / "python"))
ROOT = Path(__file__).resolve().parents[1]
COURSE_MIRROR_LABS = ROOT / "course-mirror" / "labs"


def run(label: str, args: list[str], timeout: int = 60) -> tuple[bool, str]:
    """跑命令，返回 (ok, summary)。"""
    t0 = time.time()
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        ok = r.returncode == 0
        dur = (time.time() - t0) * 1000
        # 取 stderr/stdout 末 200 字符
        tail = (r.stdout + r.stderr)[-200:].replace("\n", " | ")
        return ok, f"{dur:6.0f}ms  {tail[:100]}"
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT>{timeout}s"
    except Exception as e:
        return False, f"EXC: {e}"


def main() -> None:
    print("=" * 70)
    print("  rag-program — 端到端自检")
    print("=" * 70)
    # 探测 kbchat 是否可用（决定是否跑 course-mirror labs）
    has_kbchat = True
    try:
        subprocess.run(
            [VENV_PY, "-c", "import kbchat"],
            capture_output=True, timeout=5, check=True,
        )
    except Exception:
        has_kbchat = False
    print(f"  kbchat available: {has_kbchat}")

    checks: list[tuple[str, bool, str]] = []

    # 1. 单元测试（按目录分开跑，避免 transformers 冲突）
    print("\n━━ 单元测试 (pytest per-dir) ━━")
    test_dirs = [
        "projects/p1-enterprise-kb/tests/",
        "projects/p2-code-docs-rag/tests/",
        "projects/p3-customer-service/tests/",
        "projects/p4-finance-research/tests/",
        "03-advanced-production/labs/L15-security/tests/",
    ]
    all_ok = True
    all_msg = []
    for td in test_dirs:
        ok, msg = run(td, [VENV_PY, "-m", "pytest", td, "-q", "--tb=no"], timeout=15)
        if not ok:
            all_ok = False
            all_msg.append(f"✗ {td}: {msg}")
        else:
            print(f"  ✓ {td}")
    checks.append(("pytest", all_ok, "; ".join(all_msg) or "all dirs passed"))
    if not all_ok:
        print(f"  pytest summary: {'; '.join(all_msg)}")

    # 2. course-mirror labs（16 个，可选 — 需要 kbchat 装好）
    if not has_kbchat:
        print("\n━━ course-mirror labs 跳过（kbchat 未装）━━")
    elif not COURSE_MIRROR_LABS.exists():
        print("\n━━ course-mirror labs 跳过（course-mirror/ 目录不存在）━━")
    else:
        print("\n━━ course-mirror lab run.py ━━")
        labs = []
        for stage in ["01-foundations/labs", "02-retrieval-quality/labs", "03-advanced-production/labs"]:
            for lab in sorted((COURSE_MIRROR_LABS / stage).glob("L*/run.py")):
                if lab.parent.name in ("L04-langchain-version", "L08-cross-encoder"):
                    continue
                labs.append(lab)
        for lab in labs:
            lab_timeout = 60 if lab.parent.name in ("L09-query-rewrite", "L12-agentic-rag") else 30
            ok, msg = run(lab.name, [VENV_PY, str(lab.relative_to(ROOT))], timeout=lab_timeout)
            checks.append((f"lab/{lab.parent.name}", ok, msg))
            marker = "✓" if ok else "✗"
            print(f"  {marker} {lab.parent.name:35s}  {msg}")

    # 3. 项目 smoke（企业程序本体 — 不依赖 kbchat）
    print("\n━━ 企业程序项目 smoke ━━")
    for proj in ["p2-code-docs-rag", "p3-customer-service", "p4-finance-research", "p5-multi-format-loader"]:
        smoke = ROOT / f"projects/{proj}/src/smoke.py"
        if not smoke.exists():
            print(f"  skip {proj}（无 smoke.py）")
            continue
        ok, msg = run(proj, [VENV_PY, str(smoke.relative_to(ROOT))], timeout=30)
        checks.append((f"project/{proj}", ok, msg))
        print(f"  {'✓' if ok else '✗'} {proj:30s}  {msg}")

    # 4. Hello RAG
    print("\n━━ Hello RAG ━━")
    hello = ROOT / "examples/hello_rag.py"
    ok, msg = run("hello", [VENV_PY, str(hello.relative_to(ROOT))], timeout=60)
    checks.append(("hello_rag", ok, msg))
    print(f"  {'✓' if ok else '✗'} hello_rag: {msg}")

    # 汇总
    n_pass = sum(1 for _, ok, _ in checks if ok)
    n_total = len(checks)
    print()
    print("=" * 70)
    print(f"  {n_pass}/{n_total} 通过")
    print("=" * 70)
    if n_pass < n_total:
        print("\n失败项：")
        for label, ok, msg in checks:
            if not ok:
                print(f"  ✗ {label}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
