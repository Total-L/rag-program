"""P5 accuracy eval —— 把提取结果跟 ground_truth.jsonl 对比，算 recall/precision。

指标：
- recall：ground truth 里每个 expected block 是否被找到（type + 内容关键词都匹配）
- precision：提取出的每个 block 是否有 ground truth 覆盖（没预期的就是 spurious）
- per_format_f1

阈值（threshold.yaml）：recall>=0.85, precision>=0.70
"""

from __future__ import annotations

import json
from pathlib import Path

from ..loaders import load


def _block_matches(block, exp: dict) -> bool:
    """单个 block 是否满足期望。"""
    if block.block_type.value != exp["block_type"]:
        return False
    if "page_or_sheet" in exp and str(block.page_or_sheet) != str(exp["page_or_sheet"]):
        return False
    if "must_contain" in exp:
        text = (block.text or "") + " " + (block.image_caption or "")
        if not all(kw.lower() in text.lower() for kw in exp["must_contain"]):
            return False
    if "headers_must_include" in exp:
        hdrs = " ".join(block.headers).lower()
        if not all(kw.lower() in hdrs for kw in exp["headers_must_include"]):
            return False
    if "min_rows" in exp:
        if len(block.rows) + 1 < exp["min_rows"]:  # +1 = header
            return False
    if "min_chars" in exp:
        if len(block.text) < exp["min_chars"]:
            return False
    return True


def _match_one(exp: dict, blocks) -> bool:
    return any(_block_matches(b, exp) for b in blocks)


def evaluate(fixtures_dir: Path, gt_path: Path) -> dict:
    fixtures_dir = Path(fixtures_dir)
    gt_path = Path(gt_path)
    results: list[dict] = []
    per_format: dict[str, dict] = {}

    # JSONL 但允许每条记录跨多行；用 raw_decode 一个一个抽
    text = gt_path.read_text(encoding="utf-8").strip()
    decoder = json.JSONDecoder()
    idx = 0
    records: list[dict] = []
    while idx < len(text):
        # 跳过空白
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, end = decoder.raw_decode(text, idx)
        records.append(obj)
        idx = end

    for gt in records:
        f = fixtures_dir / gt["file"]
        doc = load(f)
        blocks = doc.blocks

        # recall
        hits = sum(1 for exp in gt["expected_blocks"] if _match_one(exp, blocks))
        recall = hits / max(len(gt["expected_blocks"]), 1)

        # precision: 提取的 block 有多少"被预期覆盖"
        expected_types = {exp["block_type"] for exp in gt["expected_blocks"]}
        spurious = sum(1 for b in blocks if b.block_type.value not in expected_types)
        precision = 1.0 if not blocks else max(0.0, 1 - spurious / len(blocks))

        fmt = doc.source_format.value
        per_format.setdefault(fmt, []).append({"recall": recall, "precision": precision})
        results.append(
            {
                "file": gt["file"],
                "format": fmt,
                "blocks_extracted": len(blocks),
                "expected": len(gt["expected_blocks"]),
                "recall": round(recall, 3),
                "precision": round(precision, 3),
            }
        )

    # 汇总
    summary = {}
    for fmt, runs in per_format.items():
        n = len(runs)
        avg_recall = sum(r["recall"] for r in runs) / n
        avg_precision = sum(r["precision"] for r in runs) / n
        f1 = 2 * avg_recall * avg_precision / max(avg_recall + avg_precision, 1e-9)
        summary[fmt] = {
            "n_files": n,
            "recall": round(avg_recall, 3),
            "precision": round(avg_precision, 3),
            "f1": round(f1, 3),
        }
    overall_recall = sum(r["recall"] for r in results) / max(len(results), 1)
    overall_precision = sum(r["precision"] for r in results) / max(len(results), 1)
    return {
        "per_file": results,
        "per_format": summary,
        "overall": {
            "recall": round(overall_recall, 3),
            "precision": round(overall_precision, 3),
            "n_files": len(results),
        },
    }


def check_thresholds(report: dict, thresholds: dict) -> tuple[bool, list[str]]:
    """返回 (passed, fail_messages)。"""
    fails: list[str] = []
    ov = report["overall"]
    if ov["recall"] < thresholds["min_recall"]:
        fails.append(f"overall recall {ov['recall']} < {thresholds['min_recall']}")
    if ov["precision"] < thresholds["min_precision"]:
        fails.append(f"overall precision {ov['precision']} < {thresholds['min_precision']}")
    for fmt, m in report["per_format"].items():
        if m["recall"] < thresholds["min_per_format_recall"]:
            fails.append(f"{fmt} recall {m['recall']} < {thresholds['min_per_format_recall']}")
    return (not fails), fails


if __name__ == "__main__":
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parents[2]
    fix = here / "fixtures"
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
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
