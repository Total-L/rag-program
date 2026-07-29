"""合成客服对话与工单生成器。

用法：
    python -m datasets.generators.customer_service --out datasets/fixtures/customer_service

设计：
- FAQ 库：50 个高频问题-答案
- 工单：300 条（含状态、严重级、分类）
- 对话：1000 轮（含用户问题、客服回答、引用 FAQ id）
- 包含意图分类、转人工触发、转人工后工单生成
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

INTENTS = ["billing", "technical", "account", "refund", "shipping", "general"]

FAQ = [
    ("如何重置密码？", "account", "在登录页点击'忘记密码'，按提示操作。"),
    ("订单多久能到？", "shipping", "通常 3-5 个工作日；偏远地区 7 天。"),
    ("可以开发票吗？", "billing", "支持电子普票与专票；下单时备注即可。"),
    ("如何申请退款？", "refund", "在订单详情页点击'申请退款'，3-5 个工作日到账。"),
    ("怎么联系人工？", "general", "回复'人工'即可转接，工作时间 9:00-21:00。"),
    ("会员怎么开通？", "account", "在个人中心选择套餐并完成支付即可。"),
    ("优惠券哪里领？", "general", "在首页弹窗或'我的-优惠券'中查看。"),
    ("APP 闪退怎么办？", "technical", "请尝试：1) 重启 APP 2) 清理缓存 3) 重新安装。"),
    ("支持哪些支付方式？", "billing", "微信、支付宝、银行卡、苹果支付。"),
    ("如何修改收货地址？", "shipping", "下单前可在购物车修改；下单后联系客服。"),
    ("学生有优惠吗？", "general", "在'学生认证'入口提交资料，通过后享 9 折。"),
    ("可以海外发货吗？", "shipping", "目前仅支持中国大陆地区；港澳台需联系客服。"),
    ("发票什么时候开？", "billing", "订单完成后 3 个工作日内开具。"),
    ("什么是会员积分？", "account", "消费 1 元积 1 分，100 积分 = 1 元抵扣。"),
    ("怎么注销账号？", "account", "在'设置-账号安全'中提交注销申请，15 天冷静期后生效。"),
    ("支付失败怎么办？", "billing", "请检查网络与余额；若仍失败，联系银行或换支付方式。"),
    ("可以指定快递吗？", "shipping", "下单时可备注首选快递；最终以发货仓库为准。"),
    ("如何开发票抬头？", "billing", "在'发票管理'中维护公司抬头与税号。"),
    ("黑卡会员特权？", "account", "享 95 折、专属客服、生日礼包、优先发货。"),
    ("怎么查看物流？", "shipping", "订单详情页点击'查看物流'可看到实时轨迹。"),
] * 3  # 60 条


@dataclass
class Ticket:
    ticket_id: str
    intent: str
    severity: str
    status: str
    subject: str
    body: str
    created_at: str
    resolved_at: str | None


@dataclass
class Turn:
    turn_id: str
    intent: str
    user: str
    agent: str
    faq_id: str | None
    transferred_to_human: bool
    resolution: str


def _ticket_body(rng: random.Random, intent: str) -> str:
    subjects = {
        "billing": ["重复扣款", "发票错误", "充值未到账"],
        "technical": ["APP 闪退", "功能不可用", "登录失败"],
        "account": ["密码无法重置", "账号被盗", "信息修改"],
        "refund": ["未收到退款", "退款金额错误", "商品问题退款"],
        "shipping": ["快递延误", "包裹丢失", "地址错误"],
        "general": ["建议反馈", "使用咨询", "活动咨询"],
    }
    return rng.choice(subjects[intent])


def generate_tickets(rng: random.Random, n: int) -> list[Ticket]:
    tickets = []
    for i in range(n):
        intent = rng.choice(INTENTS)
        sev = rng.choices(["P0", "P1", "P2", "P3"], weights=[5, 15, 40, 40])[0]
        status = rng.choices(["open", "pending", "resolved", "closed"], weights=[20, 20, 40, 20])[0]
        tickets.append(Ticket(
            ticket_id=f"TCK-{i+1:04d}",
            intent=intent,
            severity=sev,
            status=status,
            subject=_ticket_body(rng, intent),
            body=f"用户反馈{rng.choice(['多次','偶尔','持续'])}出现该问题。",
            created_at=f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}T10:00:00Z",
            resolved_at="2025-01-15T10:00:00Z" if status in ("resolved", "closed") else None,
        ))
    return tickets


def generate_dialogues(rng: random.Random, n: int) -> list[Turn]:
    turns = []
    for i in range(n):
        q, intent, a = rng.choice(FAQ)
        transferred = rng.random() < 0.15
        resolution = "resolved" if not transferred else rng.choice(["pending", "escalated"])
        turns.append(Turn(
            turn_id=f"D-{i+1:05d}",
            intent=intent,
            user=q,
            agent=a if not transferred else "正在为您转接人工客服，请稍候。",
            faq_id=f"FAQ-{FAQ.index((q,intent,a))%len(FAQ)+1:03d}" if not transferred else None,
            transferred_to_human=transferred,
            resolution=resolution,
        ))
    return turns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="datasets/fixtures/customer_service")
    parser.add_argument("--tickets", type=int, default=300)
    parser.add_argument("--dialogues", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    tickets = generate_tickets(rng, args.tickets)
    turns = generate_dialogues(rng, args.dialogues)

    (out / "tickets.jsonl").write_text(
        "\n".join(json.dumps(asdict(t), ensure_ascii=False) for t in tickets) + "\n",
        encoding="utf-8",
    )
    (out / "dialogues.jsonl").write_text(
        "\n".join(json.dumps(asdict(t), ensure_ascii=False) for t in turns) + "\n",
        encoding="utf-8",
    )
    (out / "faq.jsonl").write_text(
        "\n".join(json.dumps({"q": q, "intent": intent, "a": a, "id": f"FAQ-{i+1:03d}"},
                            ensure_ascii=False) for i, (q, intent, a) in enumerate(FAQ[:60])) + "\n",
        encoding="utf-8",
    )
    print(f"✓ generated {len(tickets)} tickets + {len(turns)} dialogues + 60 FAQ → {out}/")


if __name__ == "__main__":
    main()
