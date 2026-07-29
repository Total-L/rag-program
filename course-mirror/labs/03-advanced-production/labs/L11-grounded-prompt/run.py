"""L11 — Grounded Prompting：JSON-mode 响应 + 引用校验 + 拒答。

跑：
    python 03-advanced-production/labs/L11-grounded-prompt/run.py

复用 kbchat.generation.build_grounded_prompt + validate_response。
"""
from __future__ import annotations

import json
from pathlib import Path

ART = Path("artifacts/L11")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 简化的 grounded prompt 模板 ─────────────────────────
GROUNDED_SYSTEM = """你是公司知识库助手。仅根据"证据"回答用户问题。

规则：
1. 答案必须基于证据中的事实
2. 每条事实用 [编号] 标注证据来源
3. 证据不足时回答"我无法在知识库中找到相关信息"
4. 禁止编造证据中没有的信息
5. JSON 输出：{"answer": "...", "citations": ["E1","E2"], "abstained": false}
"""


USER_TEMPLATE = """证据：
[E1] 员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天。
[E2] 病假需要 OA 申请，附医院证明。

问题：{q}

JSON："""


# ── 2. 校验：引用必须存在 + 答案不能是凭空捏造 ───────────
def validate_response(resp: dict, valid_evidence_ids: set[str]) -> tuple[bool, str]:
    """返回 (ok, reason)。"""
    if not isinstance(resp, dict):
        return False, "not a dict"
    if "answer" not in resp or "citations" not in resp:
        return False, "missing fields"
    for cid in resp["citations"]:
        if cid not in valid_evidence_ids:
            return False, f"unknown citation: {cid}"
    if resp.get("abstained"):
        return True, "abstained (allowed)"
    if not resp["answer"].strip():
        return False, "empty answer"
    return True, "ok"


# ── 3. demo（不调 LLM，只演示结构）────────────────────
def main() -> None:
    print("════════════════════════════════════════════")
    print("  L11 — Grounded Prompting")
    print("════════════════════════════════════════════\n")

    cases = [
        {
            "q": "员工每年能休多少天年假？",
            "resp": {"answer": "司龄 1 年以下 5 天，1-3 年 10 天。", "citations": ["E1"], "abstained": False},
            "valid_evidence": {"E1", "E2"},
            "expected": True,
        },
        {
            "q": "公司创始人是谁？",
            "resp": {"answer": "我无法在知识库中找到相关信息。", "citations": [], "abstained": True},
            "valid_evidence": {"E1", "E2"},
            "expected": True,
        },
        {
            "q": "（幻觉）",
            "resp": {"answer": "公司有 1000 员工，500 客户。", "citations": ["E9"], "abstained": False},  # 错误：E9 不存在
            "valid_evidence": {"E1", "E2"},
            "expected": False,  # 应被校验拦截
        },
    ]
    for c in cases:
        ok, reason = validate_response(c["resp"], c["valid_evidence"])
        marker = "✓" if ok == c["expected"] else "✗"
        print(f"{marker} Q: {c['q']}")
        print(f"   resp: {c['resp']}")
        print(f"   ok={ok} reason={reason}")
        print()

    # 复用 kbchat
    try:
        from kbchat.generation import build_grounded_prompt, validate_response as kb_validate
        prompt = build_grounded_prompt("员工每年能休多少天？", ["年假 5 天"], evidence_ids=["E1"])
        print("→ kbchat.build_grounded_prompt 已生成")
    except Exception as e:
        print(f"⚠ kbchat 不可用：{e}")

    (ART / "report.md").write_text(
        """# L11 — Grounded Prompting 报告

## 5 条铁律
1. **系统 prompt** 明确「仅基于证据」——降低幻觉
2. **JSON 输出** 强制结构，便于校验
3. **引用校验**：每条引用必须在 valid_evidence 中
4. **拒答通道**：abstained=true 优于胡编
5. **JSON-mode** + 解析失败的 fallback（kbchat.mmx_client 有实现）

## 面试题
1. JSON-mode vs free-form？前者更易校验
2. 引用全空但 answer 很长 → 必幻觉
3. abstained 阈值如何设？kbchat 用置信度
""",
        encoding="utf-8",
    )
    print(f"✓ L11 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
