"""P6 — PII / 密钥检测与脱敏（审计判定为 critical 的缺口）。

现状问题（审计原文）：企业上线 KB 助手不可能没有 redact-on-write 或 redact-on-retrieve；
本仓库原来完全依赖**手写的 frontmatter `sensitivity:` 标签**，
一旦文档里夹了身份证号、银行卡、生产库密码，它就会被嵌入向量库、
被召回、被塞进 prompt、还会被写进 trace 日志。

三道防线（缺一不可）：

    redact_for_index   → 写库前脱敏：向量库里根本不存明文 PII
    redact_for_trace   → 写日志前脱敏：即使不入库，也不能进 trace/observability
    classify_sensitivity → 按命中的 PII 类型**自动**升级敏感度，
                           不再只信人手写的标签

为什么"检测"和"脱敏"要分开：
有些场景要保留可检索性（"张三的报销单" 得能被搜到），
所以支持 MASK（部分遮蔽，保留尾号）和 HASH（完全替换但同值同 hash，可做等值匹配）两种策略。

⚠️ 边界声明：正则脱敏能覆盖结构化 PII（号码、密钥、邮箱），
   **覆盖不了**自由文本里的姓名/地址/病情这类非结构化 PII。
   真上生产要接 Presidio / NER 模型。本模块不假装解决了全部问题。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum


class PiiType(StrEnum):
    """检测到的 PII / 密钥类别。"""

    CN_MOBILE = "cn_mobile"
    CN_ID_CARD = "cn_id_card"
    BANK_CARD = "bank_card"
    EMAIL = "email"
    IPV4 = "ipv4"
    JWT = "jwt"
    API_KEY = "api_key"
    DB_DSN = "db_dsn"
    PRIVATE_KEY = "private_key"


class Strategy(StrEnum):
    MASK = "mask"  # 138****5678 —— 保留可读性与尾号
    HASH = "hash"  # <CN_MOBILE:9f86d081> —— 同值同 hash，可等值匹配
    DROP = "drop"  # <CN_MOBILE> —— 彻底丢弃


# 敏感度权重：命中越严重的 PII，文档敏感度自动升得越高
_SEVERITY: dict[PiiType, int] = {
    PiiType.EMAIL: 1,
    PiiType.IPV4: 1,
    PiiType.CN_MOBILE: 2,
    PiiType.CN_ID_CARD: 3,
    PiiType.BANK_CARD: 3,
    PiiType.JWT: 3,
    PiiType.API_KEY: 3,
    PiiType.DB_DSN: 3,
    PiiType.PRIVATE_KEY: 3,
}

# ── 检测规则 ──────────────────────────────────────────
# 注意 negative lookbehind/ahead：避免把长数字串里的一段误当手机号/银行卡
_PATTERNS: list[tuple[PiiType, re.Pattern[str]]] = [
    # 私钥块要最先匹配（内部会含 base64，可能被其它规则切碎）
    (
        PiiType.PRIVATE_KEY,
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    ),
    # 数据库连接串（含密码）—— 生产事故高发区
    (
        PiiType.DB_DSN,
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)"
            r"(?:\+\w+)?://[^\s:@/]+:[^\s@]+@[^\s/]+(?:/\S*)?",
            re.I,
        ),
    ),
    (PiiType.JWT, re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")),
    # 常见云/服务密钥前缀
    (
        PiiType.API_KEY,
        re.compile(
            r"\b(?:AKIA[0-9A-Z]{16}"
            r"|sk-[A-Za-z0-9]{16,}"
            r"|ghp_[A-Za-z0-9]{20,}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    (
        PiiType.CN_ID_CARD,
        re.compile(
            r"(?<!\d)(?:[1-9]\d{5})(?:19|20)\d{2}"
            r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
    ),
    (
        PiiType.BANK_CARD,
        re.compile(r"(?<!\d)(?:4\d{15}|5[1-5]\d{14}|62\d{14,17}|3[47]\d{13})(?!\d)"),
    ),
    (PiiType.CN_MOBILE, re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    (PiiType.EMAIL, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    (
        PiiType.IPV4,
        re.compile(
            r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])"
        ),
    ),
]


@dataclass
class PiiHit:
    pii_type: PiiType
    value: str
    start: int
    end: int


@dataclass
class RedactionReport:
    """脱敏结果 + 可观测的命中统计。"""

    text: str
    hits: list[PiiHit] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.hits

    @property
    def max_severity(self) -> int:
        return max((_SEVERITY[h.pii_type] for h in self.hits), default=0)


def detect(text: str) -> list[PiiHit]:
    """检测但不改写。用于"只想知道有没有"的场景（如数据质量门）。

    重叠处理：按规则顺序先到先得，已被占用的区间不再匹配 ——
    避免 DSN 里的密码又被 API_KEY 规则切一刀，产出乱码。
    """
    hits: list[PiiHit] = []
    taken: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= ts or s >= te) for ts, te in taken)

    for ptype, pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if overlaps(s, e):
                continue
            taken.append((s, e))
            hits.append(PiiHit(ptype, m.group(0), s, e))
    return sorted(hits, key=lambda h: h.start)


def _mask(value: str, ptype: PiiType) -> str:
    """部分遮蔽，保留可读线索（尾号/域名）。"""
    if ptype == PiiType.EMAIL and "@" in value:
        name, domain = value.split("@", 1)
        keep = name[:1] if name else ""
        return f"{keep}{'*' * max(len(name) - 1, 3)}@{domain}"
    if ptype in (PiiType.CN_MOBILE, PiiType.BANK_CARD, PiiType.CN_ID_CARD):
        if len(value) > 7:
            return f"{value[:3]}{'*' * (len(value) - 7)}{value[-4:]}"
    return "*" * len(value)


def redact(
    text: str,
    *,
    strategy: Strategy = Strategy.MASK,
    only: set[PiiType] | None = None,
) -> RedactionReport:
    """脱敏。从后往前替换，避免位移错乱。"""
    hits = detect(text)
    if only:
        hits = [h for h in hits if h.pii_type in only]
    out = text
    for h in reversed(hits):
        if strategy is Strategy.MASK:
            rep = _mask(h.value, h.pii_type)
        elif strategy is Strategy.HASH:
            digest = hashlib.sha256(h.value.encode()).hexdigest()[:8]
            rep = f"<{h.pii_type.value.upper()}:{digest}>"
        else:
            rep = f"<{h.pii_type.value.upper()}>"
        out = out[: h.start] + rep + out[h.end :]
    counts: dict[str, int] = {}
    for h in hits:
        counts[h.pii_type.value] = counts.get(h.pii_type.value, 0) + 1
    return RedactionReport(text=out, hits=hits, counts=counts)


def redact_for_index(text: str) -> RedactionReport:
    """写向量库前：密钥类彻底 DROP（留 hash 都没意义且危险），个人信息 MASK。

    分两轮是刻意的：密钥泄漏是安全事故，个人信息还要保留一点可检索性。
    """
    secrets = {PiiType.PRIVATE_KEY, PiiType.DB_DSN, PiiType.JWT, PiiType.API_KEY}
    first = redact(text, strategy=Strategy.DROP, only=secrets)
    second = redact(first.text, strategy=Strategy.MASK)
    merged = dict(first.counts)
    for k, v in second.counts.items():
        merged[k] = merged.get(k, 0) + v
    return RedactionReport(text=second.text, hits=first.hits + second.hits, counts=merged)


def redact_for_trace(text: str, *, max_len: int = 200) -> str:
    """写 trace / 日志前：一律 HASH + 截断。

    L14 的 README 写了"PII 不得进 trace"这条规则却没有实现 —— 这里补上。
    """
    return redact(text, strategy=Strategy.HASH).text[:max_len]


def classify_sensitivity(text: str, declared: str = "public") -> tuple[str, list[str]]:
    """按命中的 PII **自动升级**敏感度（只升不降）。

    返回 (最终敏感度, 触发原因)。

    为什么只升不降：人手写的标签可能漏标（写 public 但正文有身份证号），
    但如果标了 restricted 我们不该因为"没检出 PII"就降级 —— 人可能知道
    机器不知道的上下文。安全侧永远选更严的那个。
    """
    order = {"public": 1, "internal": 2, "restricted": 3}
    rev = {1: "public", 2: "internal", 3: "restricted"}
    hits = detect(text)
    level = order.get(declared, 1)
    reasons: list[str] = []
    for h in hits:
        sev = _SEVERITY[h.pii_type]
        if sev > level:
            level = sev
        reasons.append(h.pii_type.value)
    final = rev[level]
    if final != declared:
        reasons = sorted(set(reasons))
    return final, sorted(set(reasons))


__all__ = [
    "PiiType",
    "Strategy",
    "PiiHit",
    "RedactionReport",
    "detect",
    "redact",
    "redact_for_index",
    "redact_for_trace",
    "classify_sensitivity",
]
