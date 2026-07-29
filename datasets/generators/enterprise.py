"""合成企业知识库文档生成器（HR / IT / 财务 / 法务 / 产品）。

用法：
    python -m datasets.generators.enterprise --out datasets/fixtures/enterprise --count 200

设计原则：
- 5 个分类（hr/it/finance/legal/product），每类 40 份默认
- 标题、正文、FAQ、流程步骤混合
- 每份带 frontmatter：category, sensitivity, owner, updated_at
- sensitivity 3 档：public / internal / restricted（用于 ACL 测试）
- 带 seed 可复现
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CATEGORIES = ["hr", "it", "finance", "legal", "product"]

HR_TOPICS = [
    ("年假政策", "员工年假标准为：司龄 < 1 年 5 天；1-3 年 10 天；3-5 年 15 天；5+ 年 20 天。", "public"),
    ("病假申请流程", "病假需在 OA 提交申请，附医院证明；3 天以内部门审批，3 天以上 HRBP 审批。", "public"),
    ("报销标准（国内）", "一线城市住宿 800 元/晚，二线 600 元，三线 400 元；餐补 100 元/天。", "public"),
    ("远程办公", "每周最多 2 天远程，需提前 1 天报备直属经理。", "public"),
    ("员工股权激励", "限制性股票分 4 年 vest，cliff 1 年，离职未 vest 部分按协议回购。", "restricted"),
    ("竞业限制补偿", "竞业限制期内，公司按月支付月平均工资 30% 作为补偿。", "restricted"),
    ("绩效考核周期", "半年度评估，每年 1 月、7 月各一次；结果与年终奖、调薪挂钩。", "internal"),
    ("招聘流程", "需求审批 → HR 筛选 → 技术面试 → 用人部门面试 → HR 终面 → 发放 offer。", "internal"),
]

IT_TOPICS = [
    ("VPN 接入", "使用公司 VPN 客户端，地址 vpn.corp.example.com；首次登录需绑定手机令牌。", "public"),
    ("密码重置", "访问 https://pass.corp.example.com，通过手机验证重置；2 小时内生效。", "public"),
    ("Mac 笔记本申请", "新员工入职标配 MacBook Pro 14 寸，需 IT 工单审批。", "public"),
    ("生产环境部署", "通过 GitLab CI 部署；需 PR 合并 + 至少 1 个 reviewer approve。", "internal"),
    ("数据库访问", "生产数据库需通过堡垒机；任何查询需工单申请 + DBA 审批。", "restricted"),
    ("事故响应", "事故分 P0–P3；P0 立即拉群，30 分钟内 mitigation，24 小时内 RCA。", "internal"),
    ("代码规范", "PEP 8 + 100 行函数上限；提交前必须通过 ruff + mypy。", "internal"),
    ("服务端口", "对外只开 80/443；内网服务禁止暴露公网。", "internal"),
]

FINANCE_TOPICS = [
    ("发票申请", "差旅发票通过 OA → 财务 → 报销；30 天内提交，过期不予受理。", "public"),
    ("预算审批", "单笔 < 5 万 部门负责人批；5-50 万 财务总监批；50 万+ CFO 批。", "internal"),
    ("付款流程", "网银付款需双人复核；U 盾 + 短信验证码。", "restricted"),
    ("税务申报", "增值税月报、季报；个税按月代扣代缴。", "restricted"),
    ("费用报销标准", "差旅住宿同 HR 标准；客户接待人均 300 元以内需附发票与说明。", "public"),
    ("固定资产", "单价 > 2000 元、使用 > 1 年入固定资产；每年盘点。", "internal"),
]

LEGAL_TOPICS = [
    ("合同审批", "标准合同可直接签；非标合同需法务 review，48 小时内反馈。", "internal"),
    ("数据合规", "个人信息处理遵循 PIPL；跨境传输需安全评估。", "restricted"),
    ("知识产权", "员工在职期间的成果归公司；离职 1 年内相关发明需通知公司。", "restricted"),
    ("客户保密协议", "NDA 期限通常 3 年；保密信息不得用于协议目的之外。", "internal"),
    ("诉讼处理", "收到律师函 24 小时内转法务；员工不得自行回复。", "restricted"),
]

PRODUCT_TOPICS = [
    ("产品路线图", "Q1 重点：RAG 评测；Q2 重点：Agentic RAG；Q3 重点：多模态。", "internal"),
    ("用户反馈分级", "P0: 1 小时内响应；P1: 当日；P2: 3 日；P3: 周。", "internal"),
    ("发版流程", "周二封板 → 周三灰度 10% → 周四 50% → 周五 100%。", "internal"),
    ("A/B 测试", "新功能上线需 A/B 至少 7 天，p < 0.05 且效果 ≥ 3% 才全量。", "internal"),
    ("客户案例", "Acme 公司用我们的 RAG 后，客服工单下降 35%。", "public"),
]


@dataclass
class SyntheticDoc:
    doc_id: str
    title: str
    category: str
    sensitivity: str
    owner: str
    updated_at: str
    content: str
    keywords: list[str] = field(default_factory=list)


def _gen_content(rng: random.Random, title: str, body: str) -> str:
    """生成 markdown 文档，包含标题、段落、列表、FAQ 块。"""
    faqs = [
        (f"什么是 {title}？", body),
        (f"谁负责 {title}？", "由对应部门 owner 负责；具体问题请联系部门邮箱。"),
        (f"{title} 的例外情况？", "特殊场景需走特批流程，提交 case-by-case 申请。"),
    ]
    lines = [f"# {title}", "", body, "", "## 适用对象", "", "- 全员", "- 试用期员工按比例适用", ""]
    lines += ["## 常见问题", ""]
    for q, a in faqs:
        lines.append(f"### {q}")
        lines.append("")
        lines.append(a)
        lines.append("")
    lines.append("## 更新记录")
    lines.append("")
    lines.append(f"- {datetime.now(timezone.utc).date().isoformat()}: 初版")
    return "\n".join(lines)


def generate(category: str, count: int, rng: random.Random) -> list[SyntheticDoc]:
    pool_map = {
        "hr": HR_TOPICS, "it": IT_TOPICS,
        "finance": FINANCE_TOPICS, "legal": LEGAL_TOPICS, "product": PRODUCT_TOPICS,
    }
    pool = pool_map[category]
    docs: list[SyntheticDoc] = []
    base_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(count):
        title, body, sensitivity = rng.choice(pool)
        # 允许 topic 重复但 title 用计数后缀避免冲突
        full_title = f"{title}（{category.upper()}-{i+1:03d}）"
        # keyword 是金标匹配的关键
        kws = [w for w in title.replace("（", " ").replace("）", " ").split() if len(w) > 1][:5]
        owner = f"{category}-owner{rng.randint(1,3)}@example.com"
        updated = (base_date + timedelta(days=rng.randint(0, 600))).date().isoformat()
        docs.append(SyntheticDoc(
            doc_id=f"{category}-{i+1:03d}",
            title=full_title,
            category=category,
            sensitivity=sensitivity,
            owner=owner,
            updated_at=updated,
            content=_gen_content(rng, title, body),
            keywords=kws,
        ))
    return docs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="datasets/fixtures/enterprise")
    parser.add_argument("--count", type=int, default=200, help="每分类份数（默认 200）")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    all_docs: list[SyntheticDoc] = []
    for cat in CATEGORIES:
        docs = generate(cat, args.count, rng)
        for d in docs:
            (out / f"{d.doc_id}.md").write_text(
                f"---\ncategory: {d.category}\nsensitivity: {d.sensitivity}\n"
                f"owner: {d.owner}\nupdated_at: {d.updated_at}\n---\n\n{d.content}",
                encoding="utf-8",
            )
        all_docs.extend(docs)

    (out / "manifest.jsonl").write_text(
        "\n".join(json.dumps(asdict(d), ensure_ascii=False) for d in all_docs) + "\n",
        encoding="utf-8",
    )
    print(f"✓ generated {len(all_docs)} docs → {out}/")
    print(f"  by category: {dict.fromkeys(CATEGORIES, args.count)}")
    by_sens: dict[str, int] = {}
    for d in all_docs:
        by_sens[d.sensitivity] = by_sens.get(d.sensitivity, 0) + 1
    print(f"  by sensitivity: {by_sens}")


if __name__ == "__main__":
    main()
