"""L20 — PII / 密钥检测 + 脱敏。

教学目标：
1. 看到 9 类 PII / 密钥的正则模式
2. 看到三道防线：写库前（MASK/DROP）vs 写 trace 前（HASH）vs 自动升级 sensitivity
3. 看到 ★ **顺序敏感** —— classify_sensitivity 必须在 redact_for_index **之前**

生产实现：`projects/p6-ingestion-platform/src/pii.py`

运行：
    python 05-data-engineering/labs/L20-pii-redaction/run.py

输出：
- artifacts/L20/redaction_samples.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ART = Path("artifacts/L20")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 9 类 PII / 密钥模式（精简版） ─────────────────────
PATTERNS = {
    "CN_MOBILE": (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "MASK", "keep_tail4"),
    "CN_ID_CARD": (re.compile(r"(?<!\d)[1-9]\d{16}[\dXx](?!\d)"), "MASK", "keep_tail4"),
    "BANK_CARD": (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "MASK", "keep_tail4"),
    "EMAIL": (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "MASK", "keep_full"),
    "IPV4": (re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"), "MASK", "keep_full"),
    "JWT": (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "DROP", None),
    "API_KEY": (re.compile(r"(?i)(?:api[_-]?key|secret|token)[\"':= ]+([A-Za-z0-9_-]{20,})"), "DROP", None),
    "DB_DSN": (re.compile(r"(?i)(?:postgres|mysql|mongodb)://[^\s]+:[^\s]+@[^\s]+"), "DROP", None),
    "PRIVATE_KEY": (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "DROP", None),
}


def mask_keep_tail4(s: str) -> str:
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


@dataclass
class RedactionResult:
    text: str
    findings: list[tuple[str, str, str]]  # (type, action, sample)
    clean: bool


def redact_for_index(text: str) -> RedactionResult:
    """写库前的脱敏：密钥类 DROP，个人信息 MASK（保留尾号）。"""
    findings: list[tuple[str, str, str]] = []
    out = text
    for name, (pat, action, mask) in PATTERNS.items():
        for m in pat.finditer(out):
            findings.append((name, action, m.group(0)[:30] + ("…" if len(m.group(0)) > 30 else "")))
            if action == "DROP":
                out = pat.sub("[REDACTED]", out)
            elif mask == "keep_tail4":
                out = pat.sub(lambda mo: mask_keep_tail4(mo.group(0)), out)
            elif mask == "keep_full":
                out = pat.sub("[EMAIL]", out)  # email → 整个替换，不留可识别前缀
    return RedactionResult(text=out, findings=findings, clean=len(findings) == 0)


def redact_for_trace(text: str) -> str:
    """写 trace 前的脱敏：一律 HASH + 截断，永不留原文。"""
    h = lambda s: f"#{hash(s) & 0xFFFFFFFF:08x}"
    for name, (pat, _, _) in PATTERNS.items():
        text = pat.sub(lambda mo: h(mo.group(0)), text)
    return text


def classify_sensitivity(text: str, base: str = "public") -> tuple[str, list[str]]:
    """自动升级敏感度（只升不降）。"""
    reasons: list[str] = []
    final = base
    for name, (pat, action, _) in PATTERNS.items():
        if pat.search(text):
            reasons.append(name)
            if name in ("CN_ID_CARD", "BANK_CARD", "JWT", "API_KEY", "DB_DSN", "PRIVATE_KEY"):
                final = "restricted"
            elif final != "restricted":
                final = "internal"
    return final, reasons


# ── 2. 演示 ──────────────────────────────────────────────
SAMPLE = """
# 项目交接备忘

联系人：13800138000（王工程师）
身份证号：110101199003078888
邮箱：alice@corp.example.com
工单系统登录：http://10.0.5.123:8080

数据库连接：postgres://admin:Sup3rS3cret@db.corp.example.com:5432/prod
临时 API key：REDACTED_OPENAI_KEY_PLACEHOLDER

JWT：eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.signature_xyz

薪资：年薪 50 万（含期权）
"""


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L20 — PII / 密钥检测 + 脱敏")
    print("════════════════════════════════════════════\n")

    print("─── ① 顺序敏感：classify BEFORE redact ───")
    final, reasons = classify_sensitivity(SAMPLE, base="public")
    print(f"  base=public → upgraded to {final}  原因={reasons}")
    print("  ★ 如果先 redact 再 classify，证件号已被遮蔽 → 错判成 public")

    print("\n─── ② redact_for_index（写库前）───")
    result = redact_for_index(SAMPLE)
    print(f"  发现 {len(result.findings)} 处 PII/密钥：")
    for typ, action, sample in result.findings:
        print(f"    {action:6s} {typ:15s} {sample}")
    print("\n--- 脱敏后正文 ---")
    print(result.text)

    print("\n─── ③ redact_for_trace（写日志前）───")
    print(redact_for_trace(SAMPLE)[:200])

    (ART / "samples.md").write_text(
        f"# L20 — PII 脱敏 样例\n\n"
        f"## 原文\n```\n{SAMPLE}\n```\n\n"
        f"## 脱敏后（写库）\n```\n{result.text}\n```\n\n"
        f"## 自动分级\nbase=public → **{final}**  原因={reasons}\n\n"
        f"## 三道防线\n"
        f"1. **写库前**：密钥 DROP、个人信息 MASK（保留尾号）\n"
        f"2. **写 trace 前**：一律 HASH + 截断\n"
        f"3. **自动升级 sensitivity**：classify_sensitivity 只升不降\n\n"
        f"## 顺序\nclassify_sensitivity 必须在 redact_for_index **之前** ——\n"
        f"一旦脱敏证件号被遮蔽，分类器就检不出来了。\n\n"
        f"## 边界\n正则覆盖结构化 PII，**覆盖不了**自由文本里的姓名/地址/病情。\n"
        f"真上生产要接 Presidio / NER。本模块不假装解决了全部问题。\n",
        encoding="utf-8",
    )
    print(f"\n✓ 报告 → {ART / 'samples.md'}")


if __name__ == "__main__":
    main()