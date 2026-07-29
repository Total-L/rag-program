"""生成 3 张含关键词的 PNG 图片，供 OCR 管线测试用。

- chart_apac.png：APAC 区域 bar chart（含 "APAC 240M Q3 2024" 等关键词）
- chart_growth.png：增长率 chart（含 "Growth 32% APAC Region"）
- hero_laptop.png：HTML 用的产品 hero（"Pro Laptop $1499"）

OCR 真实表现：低分辨率/带噪时可能漏字符。设计时故意让关键词冗余多份
（标题 + 图例 + 注释），保证 OCR 至少命中其中 1-2 个。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets"
OUT.mkdir(parents=True, exist_ok=True)


def _font(size: int = 22) -> ImageFont.ImageFont:
    # macOS 自带字体，避免依赖
    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def make_apac_chart() -> Path:
    out = OUT / "chart_apac.png"
    img = Image.new("RGB", (640, 360), "white")
    d = ImageDraw.Draw(img)

    # 标题
    d.text((20, 12), "Q3 2024 APAC Revenue Chart", font=_font(26), fill="black")
    # 坐标轴（视觉占位）
    d.rectangle([60, 70, 580, 300], outline="black", width=2)
    # 三根柱子（数字标在柱子上方）
    bars = [
        ("Japan", 140, 120),
        ("China", 180, 195),
        ("Korea", 220, 95),
    ]
    bar_w = 80
    for _i, (region, _x, h) in enumerate(bars):
        x0 = 100 + _i * 130
        y0 = 300 - h
        d.rectangle([x0, y0, x0 + bar_w, 300], fill="#4477AA", outline="black")
        d.text((x0 + 10, y0 - 28), f"${h // 2}M", font=_font(18), fill="black")
        d.text((x0 + 5, 305), region, font=_font(16), fill="black")
    # 图例
    d.text((20, 320), "Total APAC revenue 240M USD, Growth 32% YoY", font=_font(16), fill="gray")

    img.save(out, "PNG")
    return out


def make_growth_chart() -> Path:
    out = OUT / "chart_growth.png"
    img = Image.new("RGB", (640, 320), "white")
    d = ImageDraw.Draw(img)

    d.text((20, 12), "Regional Growth Rate 2024", font=_font(26), fill="black")
    # 4 个百分比标记
    items = [
        ("APAC", 32),
        ("North America", 12),
        ("Europe", 9),
        ("LATAM", 15),
    ]
    for i, (region, pct) in enumerate(items):
        y = 70 + i * 60
        d.text((30, y - 5), f"{region}:", font=_font(20), fill="black")
        d.rectangle([150, y, 150 + pct * 10, y + 30], fill="#22AA22", outline="black")
        d.text((150 + pct * 10 + 8, y), f"Growth {pct}%", font=_font(18), fill="darkgreen")
    # 底部说明
    d.text((20, 290), "Highest growth: APAC region at 32%", font=_font(16), fill="gray")

    img.save(out, "PNG")
    return out


def make_hero() -> Path:
    out = OUT / "hero_laptop.png"
    img = Image.new("RGB", (640, 320), "white")
    d = ImageDraw.Draw(img)

    # 模拟笔记本电脑外形
    d.rectangle([80, 60, 560, 220], outline="black", width=3, fill="#E8E8E8")
    d.rectangle([100, 80, 540, 200], fill="#2C3E50", outline="black", width=2)
    # 屏幕里的文字
    d.text((120, 105), "Pro Laptop X1", font=_font(28), fill="white")
    d.text((120, 145), "Price $1499 USD", font=_font(20), fill="#CCCCCC")
    d.text((120, 175), "Featured product 2024", font=_font(16), fill="#CCCCCC")
    # 底座
    d.rectangle([60, 220, 580, 250], fill="#888888", outline="black", width=2)
    # 标题（也是 OCR 关键词区）
    d.text((20, 270), "Pro Laptop Hero Image - Catalog SKU A-001", font=_font(18), fill="black")

    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    paths = [make_apac_chart(), make_growth_chart(), make_hero()]
    for p in paths:
        size = p.stat().st_size
        print(f"  ✓ {p.name:20s} {size:>8d} bytes")
    print(f"\n资产目录: {OUT}")
