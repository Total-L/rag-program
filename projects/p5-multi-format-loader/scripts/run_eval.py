"""P5 eval 入口。

跑：
    python projects/p5-multi-format-loader/scripts/run_eval.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # 让 src 当包导入

from src.eval.accuracy import check_thresholds, evaluate  # noqa: E402

if __name__ == "__main__":
    fix = HERE.parent / "fixtures"
    gt = fix / "ground_truth.jsonl"
    report = evaluate(fix, gt)

    print("=" * 70)
    print("  P5 — Extraction Accuracy Report")
    print("=" * 70)
    print(f"\n{'file':15s} {'blocks':>6s} {'recall':>8s} {'precision':>10s}")
    print("-" * 70)
    for r in report["per_file"]:
        print(
            f"  {r['file']:13s} {r['blocks_extracted']:>6d} {r['recall']:>8.3f} {r['precision']:>10.3f}"
        )
    print("\nPer-format:")
    for fmt, m in report["per_format"].items():
        print(
            f"  {fmt:8s}  n={m['n_files']}  recall={m['recall']:.3f}  precision={m['precision']:.3f}  f1={m['f1']:.3f}"
        )
    print(
        f"\nOverall: recall={report['overall']['recall']:.3f}  precision={report['overall']['precision']:.3f}"
    )

    thresholds = {"min_recall": 0.85, "min_precision": 0.70, "min_per_format_recall": 0.50}
    passed, fails = check_thresholds(report, thresholds)
    if passed:
        print("\n✓ THRESHOLD PASS")
    else:
        print("\n✗ THRESHOLD FAIL:")
        for f_msg in fails:
            print(f"  - {f_msg}")
        sys.exit(1)
