"""Mock Interview 抽样 + 自测。

用法：
    python interview/mock_runner.py --sample 10
    python interview/mock_runner.py --all
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
QUESTIONS_PATH = ROOT / "questions.md"


def parse_questions(path: Path) -> list[dict]:
    """极简解析 questions.md → [{q, slice, answer_hint}]。"""
    text = path.read_text(encoding="utf-8")
    qs = []
    for m in re.finditer(r"\*\*(Q\d+)\. ([^*]+)\*\*\nA\. ([^\n]+)", text):
        qid, q, a = m.group(1), m.group(2).strip(), m.group(3).strip()
        # 推断 slice（找最近的 "##" 标题）
        start = m.start()
        section = text.rfind("##", 0, start)
        slice_match = re.search(r"## ([^\n]+)", text[section:start])
        slice_ = slice_match.group(1) if slice_match else "?"
        qs.append({"id": qid, "q": q, "hint": a, "slice": slice_})
    return qs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=10)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    qs = parse_questions(QUESTIONS_PATH)
    print(f"→ 解析 {len(qs)} 道题")

    if args.all:
        sample = qs
    else:
        rng = random.Random(args.seed)
        sample = rng.sample(qs, min(args.sample, len(qs)))

    print(f"\n=== 抽 {len(sample)} 题 ===\n")
    for i, q in enumerate(sample, 1):
        print(f"━━━ Q{i}/{len(sample)} ━━━")
        print(f"  [{q['slice']}]")
        print(f"  Q: {q['q']}")
        print(f"  (提示: {q['hint'][:80]}...)")
        print()

    print("=== 自评 ===")
    for i, q in enumerate(sample, 1):
        print(f"Q{i}: ", end="")
        score = input("⭐ (1-5): ").strip() or "0"
        print(f"  [{q['id']}] {q['q'][:50]} 得分 {score}")


if __name__ == "__main__":
    main()
