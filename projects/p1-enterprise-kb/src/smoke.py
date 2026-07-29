"""P1 smoke test：端到端跑 1 个问题。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from acl import User
from pipeline import P1Pipeline


def main() -> None:
    print("════════════════════════════════════════════")
    print("  P1 — Enterprise KB 烟雾测试")
    print("════════════════════════════════════════════\n")

    user = User(user_id="alice", role="manager")
    p = P1Pipeline(user=user)
    print(f"→ 用户: {user.user_id} ({user.role})，可见级别 ≤ internal")
    p.index_corpus()
    print(f"→ 索引 chunks: {len(p.index)}")

    for q in [
        "员工每年能休多少天年假？",
        "P0 事故 30 分钟内如何处理？",
        "公司的发版流程是什么？",
    ]:
        r = p.ask(q)
        print(f"\nQ: {q}")
        print(f"A: {r.answer[:200]}")
        print(f"  引用: {r.citations}")
        print(f"  拒答: {r.abstained}  延迟: {r.latency_ms:.0f}ms")

    # 写结果
    out = Path("artifacts/P1/smoke.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "user": {"id": user.user_id, "role": user.role},
                "indexed_chunks": len(p.index),
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ P1 smoke 完成 → {out}")


if __name__ == "__main__":
    main()
