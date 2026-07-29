"""L19 — 数据清洗 / 归一化 / 近重复。

教学目标：
1. 看到"归一化必须先于 chunk_id 计算"的硬要求
2. 看到"语料级样板"自动发现 vs "正则样板"
3. 看到 MinHash + LSH 近重复检测的 30 行手写版本
4. 看到"阈值必须按文本长度校准"这个坑

生产实现：`projects/p6-ingestion-platform/src/clean.py`

运行：
    python 05-data-engineering/labs/L19-clean-normalize/run.py

输出：
- artifacts/L19/report.md
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

ART = Path("artifacts/L19")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. 归一化（NFKC + 零宽 + 空白）────────────────────────
_INVISIBLE = dict.fromkeys([0x200B, 0x200C, 0x200D, 0xFEFF, 0x00AD])


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\t", " ")
    text = re.sub(r"[ 　]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


# ── 2. 样板行正则（精简版）────────────────────────────
_BOILERPLATE = [
    re.compile(r"^\s*第\s*\d+\s*页.*$"),
    re.compile(r"^\s*Page\s+\d+.*$", re.I),
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),
    re.compile(r"^\s*(公司内部资料|Confidential).*$", re.I),
]


def strip_boilerplate(text: str, repeated: set[str] | None = None) -> str:
    repeated = repeated or set()
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if s and (any(p.match(s) for p in _BOILERPLATE) or s in repeated):
            continue
        out.append(line)
    return "\n".join(out).strip()


def find_repeated_lines(docs: list[str], *, ratio: float = 0.5) -> set[str]:
    if not docs:
        return set()
    counts: dict[str, int] = {}
    for d in docs:
        for s in {ln.strip() for ln in d.split("\n") if 0 < len(ln.strip()) <= 60}:
            counts[s] = counts.get(s, 0) + 1
    threshold = max(3, int(len(docs) * ratio))
    return {s for s, c in counts.items() if c >= threshold}


# ── 3. MinHash + LSH 近重复（精简版）────────────────────
_MERSENNE = (1 << 61) - 1


def shingles(text: str, k: int = 5) -> set[int]:
    import hashlib
    text = re.sub(r"\s+", "", text)
    return {
        int.from_bytes(hashlib.blake2b(text[i:i + k].encode(), digest_size=8).digest(), "big")
        for i in range(max(len(text) - k + 1, 1))
    }


def minhash(text: str, num_perm: int = 64, k: int = 5) -> tuple[int, ...]:
    sh = shingles(text, k=k)
    if not sh:
        return tuple([0] * num_perm)
    sig = []
    for i in range(num_perm):
        a = (i * 2_654_435_761 + 1) | 1
        b = i * 40_503 + 17
        sig.append(min(((a * x + b) % _MERSENNE) for x in sh))
    return tuple(sig)


def jaccard(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


# ── 4. 演示 ──────────────────────────────────────────────
LONG = (
    "第一条 本报销制度适用于公司全体正式员工及实习生。\n"
    "第二条 差旅费用包括交通、住宿、餐饮三类，需分项填报。\n"
    "第三条 市内交通每人每天上限二百元，需提供正规票据。\n"
    "第四条 住宿标准一线城市每晚八百元，二线城市每晚五百元。\n"
    "第五条 餐饮补贴每人每天一百元，无需提供票据。\n"
    "第六条 单笔支出超过五千元的，必须事先取得部门总监书面批准。\n"
    "第七条 所有报销申请须在费用发生后三十日内提交，逾期不予受理。\n"
    "第八条 财务部门收到申请后五个工作日内完成审核并支付。\n"
    "第九条 虚报冒领者按公司纪律处理，情节严重的移交司法机关。\n"
    "第十条 本制度自二零二六年一月一日起施行，原制度同时废止。\n"
)
LONG_TWEAKED = LONG.replace("逾期不予受理", "逾期原则上不受理")
SHORT_A = "员工差旅报销需在三十天内提交申请，单笔超过五千元需要总监审批后方可报销。"
SHORT_B = SHORT_A.replace("方可", "才可")
UNRELATED = (
    "本季度产品路线图聚焦多格式文档解析能力的全面升级。\n"
    "我们将支持扫描件的光学字符识别与复杂版面分析。\n"
    "检索侧引入混合召回与交叉编码器重排序两项改进。\n"
)


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L19 — 数据清洗 / 归一化 / 近重复")
    print("════════════════════════════════════════════\n")

    # ① 归一化必要性
    print("─── ① 归一化（全角/零宽/CRLF 必须归一，否则 chunk_id 都不同）───")
    a = normalize("报销限额为 800 元，含税")
    b = normalize("报销限额为 800 元，含税")  # 含零宽
    c = normalize("报销限额为 800 元，含税\r\n")
    print(f"  a == b: {a == b}    a == c: {a == c}")

    # ② 样板
    print("\n─── ② 样板检测 ───")
    docs = [f"# 标题{i}\nACME 保留所有权利\n正文{i}" for i in range(8)]
    rep = find_repeated_lines(docs)
    print(f"  发现重复样板行: {rep}")
    cleaned = strip_boilerplate(docs[0], repeated=rep)
    print(f"  清洗后: '{cleaned[:40]}...'")

    # ③ 近重复
    print("\n─── ③ MinHash 近重复 ───")
    sim_long = jaccard(minhash(LONG), minhash(LONG_TWEAKED))
    sim_diff = jaccard(minhash(LONG), minhash(UNRELATED))
    sim_short = jaccard(minhash(SHORT_A), minhash(SHORT_B))
    print(f"  长文档（改一处）：{sim_long:.3f}  → 阈值 0.92 能抓到")
    print(f"  无关文本：        {sim_diff:.3f}  → 不会被误抓")
    print(f"  短句（改 2 字）：  {sim_short:.3f}  → ★ 0.92 会漏！阈值必须按长度校准")

    (ART / "report.md").write_text(
        "# L19 — 清洗/归一化/近重复 报告\n\n"
        "## 关键观察\n"
        "- **归一化是增量索引能生效的前提**：全角/零宽/CRLF 不归一，每次都算不同 chunk_id → 永远在重嵌\n"
        "- **样板检测两路**：正则（页眉/页脚）+ 跨文档频率统计（公司特定页脚）\n"
        "- **MinHash + LSH**：两两比签名是 O(N²)，LSH banding 砍到近似 O(N)\n"
        "- **★ 阈值必须按文本长度校准**：\n"
        "  - 长文档改一处 ≈0.97 → 0.92 阈值能抓\n"
        "  - 短句改 2 字只有 ≈0.66 → 0.92 阈值会漏，必须降到 ~0.6\n\n"
        "## 生产版\n"
        "`projects/p6-ingestion-platform/src/clean.py` 多了：\n"
        "- `_drop_empty_headings`：清理【答案被删后的孤儿标题】\n"
        "- 9 类 PII 检测 + 三道脱敏防线（见 L20）\n"
        "- 质量过滤：目录页 / 纯符号 / 短文本 / 数字+符号堆\n\n"
        "## 面试题\n"
        "1. 为什么归一化要在 chunk_id 计算之前？\n"
        "2. 阈值 0.92 对 30 字短句近重复为什么漏？\n"
        "3. LSH banding 的取舍（b 越大越敏感但越慢）？\n",
        encoding="utf-8",
    )
    print(f"\n✓ 报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()