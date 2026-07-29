"""P1 评测入口：50 题金标 + 阈值检查。

跑：
    python projects/p1-enterprise-kb/tests/run_eval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from acl import User
from pipeline import P1Pipeline  # noqa: E402

from eval import evaluate, load_golden  # noqa: E402

THRESHOLDS = {
    "citation_validity": 1.0,
    "abstention_correctness": 0.80,
    "contains_expected": 0.50,
}


def main() -> None:
    print("→ 索引 + 评测")
    p = P1Pipeline(user=User("alice", "executive"))
    p.index_corpus()
    print(f"  indexed chunks: {len(p.index)}")

    golden = load_golden(Path("datasets/golden/golden_v1.jsonl"))
    if not golden:
        print("⚠ 无金标：python -m datasets.golden.build_v1")
        sys.exit(1)
    report = evaluate(p, golden)

    print("\n━━━ 整体指标 ━━━")
    for k, v in report["overall"].items():
        print(f"  {k}: {v}")

    # 阈值检查
    fail = []
    for k, th in THRESHOLDS.items():
        actual = report["overall"].get(k, 0)
        if actual < th:
            fail.append(f"  ✗ {k}: {actual} < {th}")
        else:
            print(f"  ✓ {k}: {actual} >= {th}")

    # 写报告
    out = Path("eval/reports/p1_golden.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ 报告 {out}")

    if fail:
        print("\n未达标：")
        for f in fail:
            print(f)
        sys.exit(1)
    print("\n✓ P1 全部阈值通过")


if __name__ == "__main__":
    main()
