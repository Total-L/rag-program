"""公开数据集抽样（HotpotQA / NQ），用于多跳与开放域评测。

用法：
    python -m datasets.public.loader --dataset hotpotqa --n 100 --out datasets/public/hotpotqa_sample.jsonl

依赖：`datasets`（HuggingFace）。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def fetch_hotpotqa(n: int) -> list[dict]:
    from datasets import load_dataset
    ds = load_dataset("hotpot_qa", "distractor", split="validation", trust_remote_code=True)
    out = []
    for i, row in enumerate(ds):
        if i >= n:
            break
        out.append({
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "type": row.get("type", ""),
            "level": row.get("level", ""),
            "supporting_facts": row.get("supporting_facts", {}),
        })
    return out


def fetch_nq(n: int) -> list[dict]:
    from datasets import load_dataset
    try:
        ds = load_dataset("nq_open", split="validation", trust_remote_code=True)
    except Exception:
        ds = load_dataset("natural_questions", split="validation", trust_remote_code=True)
    out = []
    for i, row in enumerate(ds):
        if i >= n:
            break
        out.append({
            "id": str(i),
            "question": row.get("question", ""),
            "answer": (row.get("answers") or row.get("short_answers") or [""])[0],
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["hotpotqa", "nq"], required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.dataset == "hotpotqa":
        data = fetch_hotpotqa(args.n)
    else:
        data = fetch_nq(args.n)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in data) + "\n", encoding="utf-8")
    print(f"✓ {args.dataset} sample: {len(data)} → {out}")


if __name__ == "__main__":
    main()
