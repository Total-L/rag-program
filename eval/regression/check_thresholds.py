"""CI 阈值检查：所有项目指标不达标则非零退出。

用法：
    python eval/regression/check_thresholds.py

逻辑：内联阈值（与 thresholds.yaml 同步），与最近一份 RAGAS 报告对比，
任意一项不达标则退出 1。

v2 (2026-07)：字段对齐 eval.py 实际产出（详见 RCA_p1_eval_regression.md 根因 6+7 修复）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 内联阈值（与 thresholds.yaml 同步 —— YAML 是参考文档，本 dict 是 gate 真源）
THRESHOLDS = {
    "p1-enterprise-kb": {
        "hit_rate": 0.50,  # 真 doc_id 命中率
        "recall_at_10": 0.50,  # topk 命中 expected 的比例
        "mrr": 0.40,  # 首个 expected 命中的 1/排名
        "citation_validity": 0.95,  # 引用是否指向真实 chunk
        "abstention_correctness": 0.70,  # 拒答判断正确率
        "contains_expected": 0.50,  # 答案含期望关键词比例
    },
    "p2-code-docs-rag": {
        "recall_at_10": 0.70,
        "faithfulness": 0.80,
    },
    "p3-customer-service": {
        "intent_accuracy": 0.85,
        "transfer_precision": 0.80,
    },
    "p4-finance-research": {
        "recall_at_10": 0.75,
        "table_qa_accuracy": 0.70,
    },
}


def check_project(project: str, report_path: Path) -> tuple[bool, list[str]]:
    if not report_path.exists():
        return False, [f"报告不存在：{report_path}"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # 兼容新旧两种报告结构：eval.py 把字段放在 overall 里；
    # 早期版本放在 metrics 顶层（详见 RCA_p1_eval_regression.md 根因 6）
    metrics = report.get("overall") or report.get("metrics", {})
    issues: list[str] = []
    for k, v in THRESHOLDS.get(project, {}).items():
        actual = metrics.get(k)
        if actual is None:
            issues.append(f"  ! {k}: 报告缺少该指标")
            continue
        if k == "protected_path_leakage":
            if actual > v:
                issues.append(f"  ✗ {k}: {actual} > 阈值 {v}（必须为 0）")
        else:
            if actual < v:
                issues.append(f"  ✗ {k}: {actual} < 阈值 {v}")
    return (len(issues) == 0), issues


def main() -> None:
    reports_dir = Path("eval/reports")
    failures: list[str] = []
    for project in THRESHOLDS:
        # 优先按完整 project 名找（如 p1-enterprise-kb_golden.json）
        candidates = sorted(reports_dir.glob(f"*{project}*.json"), reverse=True)
        if not candidates:
            # Fallback：按 short id 找，兼容 p1_golden.json / p1_golden_latest.json
            # 这类历史命名（详见 RCA_p1_eval_regression.md 根因 2）
            short_id = project.split("-")[0]
            if short_id != project:
                candidates = sorted(reports_dir.glob(f"*{short_id}*.json"), reverse=True)
        if not candidates:
            print(f"⚠ {project}: 无报告，跳过")
            continue
        report_path = candidates[0]
        ok, issues = check_project(project, report_path)
        if ok:
            print(f"✓ {project} ({report_path.name}) 全部阈值通过")
        else:
            failures.append(project)
            print(f"✗ {project} ({report_path.name}):")
            for i in issues:
                print(i)
    if failures:
        print(f"\n{len(failures)} 个项目未通过阈值：{failures}")
        sys.exit(1)
    print("\n所有项目通过阈值。")


if __name__ == "__main__":
    main()
