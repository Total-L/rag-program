"""P3 — Customer Service 烟雾测试。

简化：意图分类 + FAQ 检索 + 转人工。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# 6 意图的关键词
INTENT_KEYWORDS = {
    "billing": ["发票", "支付", "充值", "扣款", "invoice", "pay", "billing"],
    "technical": ["闪退", "登录失败", "崩溃", "crash", "bug", "技术"],
    "account": ["密码", "账号", "登录", "注册", "注销", "account", "password"],
    "refund": ["退款", "退订", "退货", "refund"],
    "shipping": ["快递", "物流", "发货", "收货", "shipping", "delivery"],
    "general": ["咨询", "活动", "优惠券", "会员", "general"],
}

TRANSFER_TRIGGERS = ["人工", "投诉", "经理", "客服", "退款不到", "紧急"]


def classify_intent(text: str) -> tuple[str, float]:
    """返回 (intent, confidence)。"""
    text_l = text.lower()
    scores: dict[str, int] = {}
    for intent, kws in INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for kw in kws if kw.lower() in text_l)
    if max(scores.values()) == 0:
        return "general", 0.2
    best = max(scores, key=scores.get)
    conf = scores[best] / sum(scores.values())
    return best, conf


def needs_transfer(text: str) -> bool:
    return any(t in text for t in TRANSFER_TRIGGERS)


# 60 条 toy FAQ（同生成器）
FAQ = [
    ("如何重置密码？", "account", "在登录页点击'忘记密码'，按提示操作。"),
    ("订单多久能到？", "shipping", "通常 3-5 个工作日；偏远地区 7 天。"),
    ("可以开发票吗？", "billing", "支持电子普票与专票。"),
    ("如何申请退款？", "refund", "在订单详情页点击'申请退款'。"),
    ("怎么联系人工？", "general", "回复'人工'即可转接。"),
    ("会员怎么开通？", "account", "在个人中心选择套餐。"),
    ("优惠券哪里领？", "general", "在首页弹窗或'我的-优惠券'查看。"),
    ("APP 闪退怎么办？", "technical", "请尝试：1) 重启 APP 2) 清理缓存 3) 重新安装。"),
    ("支持哪些支付方式？", "billing", "微信、支付宝、银行卡、苹果支付。"),
    ("怎么注销账号？", "account", "在'设置-账号安全'中提交申请。"),
] * 6  # 60 条


def faq_search(query: str, intent: str, k: int = 2) -> list[dict]:
    """按 intent 过滤 + 关键词命中。"""
    candidates = [f for f in FAQ if f[1] == intent]
    if not candidates:
        candidates = FAQ
    scored = []
    q_words = set(re.findall(r"[\w]+", query))
    for q, intent_, a in candidates:
        score = sum(1 for w in q_words if w in q)
        scored.append((score, q, intent_, a))
    scored.sort(key=lambda x: -x[0])
    return [{"q": q, "intent": i, "a": a} for _, q, i, a in scored[:k]]


def make_ticket(user_id: str, text: str, intent: str) -> dict:
    return {
        "ticket_id": f"TCK-{user_id}-{hash(text) % 10000:04d}",
        "user_id": user_id,
        "intent": intent,
        "text": text,
        "severity": "P0" if "紧急" in text or "投诉" in text else "P2",
    }


def main() -> None:
    print("════════════════════════════════════════════")
    print("  P3 — Customer Service 烟雾测试")
    print("════════════════════════════════════════════\n")

    queries = [
        ("u1", "我想重置密码"),
        ("u2", "快递怎么还没到？我要投诉！"),
        ("u3", "APP 又闪退了"),
        ("u4", "转人工"),
        ("u5", "怎么开发票？"),
    ]
    log = []
    for user_id, q in queries:
        intent, conf = classify_intent(q)
        transfer = needs_transfer(q)
        if transfer:
            ticket = make_ticket(user_id, q, intent)
            print(f"\nQ ({user_id}): {q}")
            print(f"  意图={intent} 置信度={conf:.2f} → 转人工")
            print(f"  工单: {ticket}")
            log.append({"q": q, "intent": intent, "transfer": True, "ticket": ticket})
        else:
            hits = faq_search(q, intent)
            print(f"\nQ ({user_id}): {q}")
            print(f"  意图={intent} 置信度={conf:.2f}")
            for h in hits:
                print(f"  → FAQ: {h['q']} | 答: {h['a'][:50]}")
            log.append({"q": q, "intent": intent, "transfer": False, "hits": hits})

    out = Path("artifacts/P3/smoke.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "n_queries": len(queries),
                "transfers": sum(1 for entry in log if entry["transfer"]),
                "ok": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n✓ P3 完成 → {out}")


if __name__ == "__main__":
    main()
