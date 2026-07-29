"""P6 — 数据清洗与归一化（"怎么优化数据"的第一步）。

审计发现：本仓库原来只有**检索侧**优化（rewrite / rerank / packing），
数据侧几乎是裸的 —— 没有归一化、没有去样板、没有近重复检测、没有质量过滤。
垃圾进垃圾出，再好的 rerank 也救不回来。

本模块四件事，按流水线顺序：

    normalize_text   → 编码/全半角/零宽/空白 归一化（不归一化会导致同一段话切出不同 chunk_id）
    strip_boilerplate→ 去页眉页脚/导航/免责声明（这些会重复出现在每篇文档，污染召回）
    detect_language  → 语言路由（中英混排要用不同的切分与嵌入策略）
    NearDupIndex     → MinHash + LSH 近重复检测（精确 hash 抓不到"改了三个字"的重复）

刻意不引第三方库（`langdetect` / `datasketch`）：
本仓库的教学原则是"不只学框架，要会手写最小版本"（见 ROADMAP 全程心法），
MinHash / LSH 的核心就 40 行，手写一遍才知道它为什么会漏、阈值该怎么调。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

# ── 零宽与不可见字符：复制粘贴自 Office/网页的文档里到处都是 ─────────
_INVISIBLE = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0xFEFF, 0x00AD, 0x2060])

# 常见样板行（页眉/页脚/导航/免责声明）。企业文档模板化严重，这些行会重复几百次。
_BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*第\s*\d+\s*页(\s*/\s*共\s*\d+\s*页)?\s*$"),
    re.compile(r"^\s*Page\s+\d+\s*(of\s+\d+)?\s*$", re.I),
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),  # - 12 -
    re.compile(r"^\s*(公司内部资料|内部文件|机密文件|仅供内部使用)[，。!！]?\s*$"),
    re.compile(r"^\s*(Confidential|Internal Use Only|All Rights Reserved).*$", re.I),
    re.compile(r"^\s*版权所有\s*©?.*$"),
    re.compile(r"^\s*©\s*\d{4}.*$"),
    re.compile(r"^\s*(上一页|下一页|返回目录|回到顶部)\s*$"),
]


def normalize_text(text: str) -> str:
    """文本归一化。

    为什么这一步必须在**算 chunk_id 之前**做：
    全角"，"和半角","、带不带 BOM、\\r\\n 还是 \\n —— 都会算出不同的 sha256，
    于是每次摄取都以为"内容变了"，白白重嵌 + 制造僵尸向量。
    归一化是增量索引能真正生效的前提。
    """
    if not text:
        return ""
    # NFKC：全角→半角、兼容字符（① → 1、㈱ → (株)）统一
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", " ")
    text = re.sub(r"[ 　]{2,}", " ", text)  # 连续空格/全角空格 → 一个
    text = re.sub(r"\n{3,}", "\n\n", text)  # 3+ 空行 → 一个空行
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


@dataclass
class BoilerplateStats:
    removed_lines: int = 0
    removed_samples: list[str] = field(default_factory=list)


def strip_boilerplate(
    text: str, *, repeated_lines: set[str] | None = None
) -> tuple[str, BoilerplateStats]:
    """去样板行。

    两种样板：
    1. **模式化**的（页码、机密声明）→ 正则
    2. **跨文档重复**的（这个公司特有的页脚）→ 传 `repeated_lines`
       用 `find_repeated_lines()` 先在语料上统计出来

    第 2 种是关键：每家公司的页脚都不一样，写死正则覆盖不了，
    必须靠"在语料里出现频率极高的短行"自动发现。
    """
    stats = BoilerplateStats()
    out: list[str] = []
    repeated = repeated_lines or set()
    for line in text.split("\n"):
        s = line.strip()
        if s and (
            any(p.match(s) for p in _BOILERPLATE_PATTERNS) or (s in repeated and len(s) <= 60)
        ):
            stats.removed_lines += 1
            if len(stats.removed_samples) < 5:
                stats.removed_samples.append(s)
            continue
        out.append(line)
    # ★ 清理"孤儿标题"：正文被判为样板删掉后，只剩一个没有内容的标题。
    # 例：`### 谁负责 付款流程?` 的答案是全语料通用的样板句，被删掉后
    # 标题独自留下 —— 它没有任何信息量，却会稀释 chunk 的嵌入语义。
    out, orphans = _drop_empty_headings(out)
    stats.removed_lines += orphans
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned, stats


_ANY_HEADING = re.compile(r"^\s*#{1,6}\s+\S")


def _drop_empty_headings(lines: list[str]) -> tuple[list[str], int]:
    """删掉后面没有任何正文的标题（保留至少有内容的标题）。"""
    keep = [True] * len(lines)
    removed = 0
    for i, line in enumerate(lines):
        if not _ANY_HEADING.match(line):
            continue
        has_content = False
        for nxt in lines[i + 1 :]:
            if _ANY_HEADING.match(nxt):
                break  # 撞到下一个标题 → 中间没内容
            if nxt.strip():
                has_content = True
                break
        if not has_content:
            keep[i] = False
            removed += 1
    return [ln for ln, k in zip(lines, keep, strict=False) if k], removed


def find_repeated_lines(
    docs: list[str], *, min_docs: int = 3, max_len: int = 60, ratio: float = 0.5
) -> set[str]:
    """在语料里找"出现在过多文档中的短行" —— 这些几乎一定是页眉页脚。

    `ratio=0.5` 表示：一行出现在 ≥50% 的文档里就判定为样板。
    阈值不能太低，否则会误删真实的公共术语行。
    """
    if not docs:
        return set()
    counts: dict[str, int] = {}
    for d in docs:
        for s in {ln.strip() for ln in d.split("\n") if 0 < len(ln.strip()) <= max_len}:
            counts[s] = counts.get(s, 0) + 1
    threshold = max(min_docs, int(len(docs) * ratio))
    return {s for s, c in counts.items() if c >= threshold}


# ── 语言检测（手写：CJK / 拉丁字符占比）─────────────────────
def detect_language(text: str) -> str:
    """返回 zh / en / mixed / unknown。

    为什么要检测：中文要按字切 shingle、英文按词切；
    嵌入模型也不同（多语言 vs 英文专用）。混排文档处理错了召回会明显掉。
    比起引 `langdetect`（要下模型、对短文本还不稳），字符占比对 RAG 场景够用且可解释。
    """
    if not text:
        return "unknown"
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cjk + latin
    if total < 5:
        return "unknown"
    zh_ratio = cjk / total
    if zh_ratio >= 0.7:
        return "zh"
    if zh_ratio <= 0.1:
        return "en"
    return "mixed"


# ── 质量过滤 ──────────────────────────────────────────
@dataclass
class QualityVerdict:
    ok: bool
    reason: str = ""


def quality_check(
    text: str, *, min_chars: int = 30, max_symbol_ratio: float = 0.5
) -> QualityVerdict:
    """把明显没有检索价值的内容挡在向量库外面。

    每一条都是真实踩过的坑：目录页、纯页码页、扫描失败只剩噪点的 OCR 结果，
    这些进了库只会挤占 top-k 名额。
    """
    s = text.strip()
    if len(s) < min_chars:
        return QualityVerdict(False, f"too_short(<{min_chars})")
    if re.fullmatch(r"[\s\d\.\-_、。，,]+", s):
        return QualityVerdict(False, "digits_punct_only")
    # 目录页特征：大量 "....... 12" 形式的行。
    # 这一条必须排在符号占比之前 —— 目录页符号占比天然很高，
    # 先判符号占比会把它误报成 mostly_symbols，丢失更精确的诊断信息。
    dots = len(re.findall(r"\.{4,}\s*\d+", s))
    if dots >= 3:
        return QualityVerdict(False, "looks_like_toc")
    alnum = sum(1 for ch in s if ch.isalnum())
    if alnum / max(len(s), 1) < (1 - max_symbol_ratio):
        return QualityVerdict(False, "mostly_symbols")
    return QualityVerdict(True)


# ── 近重复检测：手写 MinHash + LSH ───────────────────────
_MERSENNE = (1 << 61) - 1  # 大素数，做 (a*x+b) mod p 的模


def shingles(text: str, *, k: int = 5, lang: str | None = None) -> set[int]:
    """把文本切成 k-gram 指纹集合。

    中文按**字** k-gram（k=5 约等于一个短语），英文按**词** k-gram。
    切错了相似度会失真：英文按字符切会把 "the" 的噪声放大。

    ⚠️ 注意返回的是 **set**：重复出现的内容不会让集合变大。
    所以"同一段话重复 8 次"的 shingle 集合 ≈ "出现 1 次"的集合 ——
    这对样板文字很多的文档是好事（重复页脚不会主导相似度），
    但也意味着**不能靠复制文本来构造"长文档"**做相似度实验。
    """
    lang = lang or detect_language(text)
    s = re.sub(r"\s+", "", text) if lang in ("zh", "mixed") else text.lower()
    if lang == "en":
        toks = re.findall(r"[a-z0-9]+", s)
        units = [" ".join(toks[i : i + k]) for i in range(max(len(toks) - k + 1, 1))]
    else:
        units = [s[i : i + k] for i in range(max(len(s) - k + 1, 1))]
    return {
        int.from_bytes(hashlib.blake2b(u.encode(), digest_size=8).digest(), "big")
        for u in units
        if u
    }


def minhash(text: str, *, num_perm: int = 64, k: int = 5) -> tuple[int, ...]:
    """MinHash 签名。

    原理：对每个哈希置换 h_i，取集合里所有元素 h_i 的**最小值**。
    两个集合的 Jaccard 相似度 ≈ 签名对应位置相等的比例。
    这就是"不用两两比原文也能估相似度"的全部魔法。
    """
    sh = shingles(text, k=k)
    if not sh:
        return tuple([0] * num_perm)
    sig = []
    for i in range(num_perm):
        # 确定性生成第 i 个置换的 (a, b)，避免依赖随机种子（可复现！）
        a = (i * 2_654_435_761 + 1) | 1
        b = i * 40_503 + 17
        sig.append(min(((a * x + b) % _MERSENNE) for x in sh))
    return tuple(sig)


def jaccard(sig_a: tuple[int, ...], sig_b: tuple[int, ...]) -> float:
    """用签名估 Jaccard 相似度。"""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b, strict=False) if x == y) / len(sig_a)


class NearDupIndex:
    """MinHash + LSH banding 近重复索引。

    为什么要 LSH 而不是两两比签名：N 篇文档两两比是 O(N²)，
    10 万文档就是 50 亿次比较。LSH 把签名切成 b 个 band，
    只有**至少一个 band 完全相同**的才成为候选 → 近似 O(N)。

    调参直觉：band 越多越敏感（召回更多候选、更慢）；
    阈值 ≈ (1/b)^(1/r)，b=16/r=4 时约 0.5，适合"抓改了几个字的重复"。

    ⚠️ 阈值必须按**文本长度**校准（实测踩过的坑）：
    k=5 字 shingle 下，一处 2 字改动会影响约 10 个 shingle。
    - 长文档（数百字以上）：2 字改动 → 相似度仍 ≈0.97，用 0.92 能抓到
    - 短句（30–40 字）：2 字改动 → 相似度只有 ≈0.66，用 0.92 会**漏掉**
    所以默认 0.85 面向"文档级"去重；要抓短文本近重复必须显式降阈值到 ~0.6。
    """

    def __init__(self, *, num_perm: int = 64, bands: int = 16, threshold: float = 0.85):
        if num_perm % bands != 0:
            raise ValueError("num_perm 必须能被 bands 整除")
        self.num_perm = num_perm
        self.bands = bands
        self.rows = num_perm // bands
        self.threshold = threshold
        self._buckets: dict[tuple[int, int], list[str]] = {}
        self._sigs: dict[str, tuple[int, ...]] = {}

    def _band_keys(self, sig: tuple[int, ...]):
        for bi in range(self.bands):
            chunk = sig[bi * self.rows : (bi + 1) * self.rows]
            yield (bi, hash(chunk))

    def add(self, key: str, text: str) -> list[tuple[str, float]]:
        """加入一条，并返回与它近重复的已有条目 [(key, similarity)]。"""
        sig = minhash(text, num_perm=self.num_perm)
        candidates: set[str] = set()
        for bk in self._band_keys(sig):
            candidates.update(self._buckets.get(bk, ()))
        dups = []
        for c in candidates:
            sim = jaccard(sig, self._sigs[c])
            if sim >= self.threshold:
                dups.append((c, sim))
        # 无论是否重复都入索引（这样能查出"一组"重复而非只有第一条）
        self._sigs[key] = sig
        for bk in self._band_keys(sig):
            self._buckets.setdefault(bk, []).append(key)
        return sorted(dups, key=lambda x: -x[1])

    def __len__(self) -> int:
        return len(self._sigs)


__all__ = [
    "normalize_text",
    "strip_boilerplate",
    "find_repeated_lines",
    "BoilerplateStats",
    "detect_language",
    "quality_check",
    "QualityVerdict",
    "shingles",
    "minhash",
    "jaccard",
    "NearDupIndex",
]
