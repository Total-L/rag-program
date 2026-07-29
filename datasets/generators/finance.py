"""合成金融研报与公告生成器（脱敏 A 股场景）。

用法：
    python -m datasets.generators.finance --out datasets/fixtures/finance

设计：
- 50 份年报（10-K 风格）：业务概览、财务摘要、风险因素、MD&A
- 30 份研报：评级、目标价、估值
- 全部脱敏：股票代码用 ticker-XXX，金额用相对量级
- 关键字段：ticker, period, rating, target_price
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

SECTORS = ["科技", "消费", "医药", "金融", "工业", "能源"]
RATINGS = ["买入", "增持", "中性", "减持", "卖出"]


@dataclass
class AnnualReport:
    doc_id: str
    ticker: str
    company: str
    sector: str
    period: str  # 2024 年报
    revenue_yoy: float
    net_profit_yoy: float
    gross_margin: float
    content: str
    tables: list[dict] = field(default_factory=list)


@dataclass
class ResearchReport:
    doc_id: str
    ticker: str
    company: str
    sector: str
    rating: str
    target_price: float
    current_price: float
    analyst: str
    publish_date: str
    content: str


def _gen_annual(rng: random.Random, idx: int) -> AnnualReport:
    ticker = f"ticker-{idx:03d}"
    company = f"公司-{idx:03d}"
    sector = rng.choice(SECTORS)
    period = f"{rng.choice([2023, 2024])} 年报"
    rev_yoy = round(rng.uniform(-0.2, 0.6), 3)
    np_yoy = round(rng.uniform(-0.5, 0.8), 3)
    gm = round(rng.uniform(0.15, 0.65), 3)
    tables = [
        {
            "title": "利润表摘要（亿元）",
            "rows": [
                ["指标", "本期", "上期", "同比"],
                ["营业收入", f"{rng.uniform(50,500):.1f}", f"{rng.uniform(50,500):.1f}", f"{rev_yoy*100:.1f}%"],
                ["归母净利润", f"{rng.uniform(5,80):.1f}", f"{rng.uniform(5,80):.1f}", f"{np_yoy*100:.1f}%"],
                ["毛利率", f"{gm*100:.1f}%", "-", "-"],
            ],
        },
    ]
    content = f"""# {company}（{ticker}）{period}

## 一、公司概况
{company} 是 {sector} 行业龙头企业之一。报告期内，公司实现营业收入同比增长 {rev_yoy*100:.1f}%，归母净利润同比增长 {np_yoy*100:.1f}%。

## 二、业务回顾
报告期内公司在核心产品线持续投入，新客户拓展超预期。海外业务收入占比提升至 {rng.randint(15,45)}%。

## 三、风险因素
- 原材料价格波动
- 汇率风险（美元结算占比 {rng.randint(10,40)}%）
- 行业政策变化

## 四、未来展望
预计 {sector} 行业 2025 年增速 {rng.randint(3,15)}%；公司计划加大研发投入至营收的 {rng.randint(5,15)}%。

## 五、关键财务数据

{tables[0]['title']}

| {tables[0]['rows'][0][0]} | {tables[0]['rows'][0][1]} | {tables[0]['rows'][0][2]} | {tables[0]['rows'][0][3]} |
|---|---|---|---|
""" + "\n".join(
        f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |" for r in tables[0]["rows"][1:]
    )
    return AnnualReport(
        doc_id=f"AR-{idx:03d}",
        ticker=ticker, company=company, sector=sector, period=period,
        revenue_yoy=rev_yoy, net_profit_yoy=np_yoy, gross_margin=gm,
        content=content, tables=tables,
    )


def _gen_research(rng: random.Random, idx: int) -> ResearchReport:
    ticker = f"ticker-{idx:03d}"
    company = f"公司-{idx:03d}"
    sector = rng.choice(SECTORS)
    rating = rng.choice(RATINGS[:3])  # 多给正面评级
    current = round(rng.uniform(10, 200), 2)
    upside = rng.uniform(-0.2, 0.5)
    target = round(current * (1 + upside), 2)
    content = f"""# {company}（{ticker}）深度研究

**评级：{rating}** | **目标价：{target}** | **当前价：{current}** | **预期收益：{upside*100:+.1f}%**

## 投资要点
- {sector} 行业景气度持续向上
- 公司核心产品市占率提升
- 估值具有吸引力（PE {rng.randint(15,40)}x）

## 估值
采用 DCF 与相对估值法：
- DCF 隐含目标价：{target * 0.95:.2f}
- 行业均值 PE：{rng.randint(20,35)}x
- 公司当前 PE：{rng.randint(15,40)}x

## 风险
- 行业景气度不及预期
- 原材料涨价
- 政策变化
"""
    return ResearchReport(
        doc_id=f"RR-{idx:03d}",
        ticker=ticker, company=company, sector=sector,
        rating=rating, target_price=target, current_price=current,
        analyst=f"分析师-{rng.randint(1,10)}",
        publish_date=f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
        content=content,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="datasets/fixtures/finance")
    parser.add_argument("--annual", type=int, default=50)
    parser.add_argument("--research", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    annuals = [_gen_annual(rng, i) for i in range(args.annual)]
    research = [_gen_research(rng, i) for i in range(args.research)]

    (out / "annual_reports.jsonl").write_text(
        "\n".join(json.dumps(asdict(a), ensure_ascii=False) for a in annuals) + "\n",
        encoding="utf-8",
    )
    (out / "research_reports.jsonl").write_text(
        "\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in research) + "\n",
        encoding="utf-8",
    )

    # 单独保存 markdown 视图（便于直接读入 P4）
    (out / "annual_reports.md").write_text(
        "\n\n---\n\n".join(a.content for a in annuals), encoding="utf-8"
    )
    (out / "research_reports.md").write_text(
        "\n\n---\n\n".join(r.content for r in research), encoding="utf-8"
    )
    print(f"✓ generated {len(annuals)} 年报 + {len(research)} 研报 → {out}/")


if __name__ == "__main__":
    main()
