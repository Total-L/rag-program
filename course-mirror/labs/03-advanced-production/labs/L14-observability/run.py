"""L14 — Observability：解析 trace.jsonl，输出仪表盘数据。

跑：
    python 03-advanced-production/labs/L14-observability/run.py

复用 kbchat.observability.trace 写 trace；本 lab 解析并生成报告。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

ART = Path("artifacts/L14")
ART.mkdir(parents=True, exist_ok=True)


def parse_traces(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def dashboard(traces: list[dict]) -> dict:
    """生成仪表盘数据。"""
    if not traces:
        return {"n": 0}

    latencies = [t.get("duration_ms", 0) for t in traces if "duration_ms" in t]
    stages = Counter(t.get("stage", "?") for t in traces)
    errors = [t for t in traces if t.get("error")]

    return {
        "n": len(traces),
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else 0,
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if len(latencies) > 1 else 0,
        "by_stage": dict(stages),
        "error_rate": round(len(errors) / max(len(traces), 1), 3),
        "error_types": Counter(t["error"] for t in errors).most_common(5),
    }


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L14 — Observability 仪表盘")
    print("════════════════════════════════════════════\n")

    # 写一些 toy traces
    toy_path = ART / "toy_traces.jsonl"
    toy_traces = []
    import random
    rng = random.Random(42)
    for i in range(100):
        toy_traces.append({
            "trace_id": f"t-{i:03d}",
            "stage": rng.choice(["retrieve", "rerank", "generate", "pack"]),
            "duration_ms": rng.randint(20, 800),
            "correlation_id": f"c-{rng.randint(0, 20):03d}",
            "error": None if rng.random() > 0.1 else rng.choice(["mmx_timeout", "chroma_lock"]),
        })
    toy_path.write_text("\n".join(json.dumps(t) for t in toy_traces), encoding="utf-8")

    # 解析
    traces = parse_traces(toy_path)
    dash = dashboard(traces)
    print(json.dumps(dash, ensure_ascii=False, indent=2))

    # 复用 kbchat trace
    try:
        from kbchat.observability import trace, timed, new_correlation_id
        cid = new_correlation_id()
        with timed("lab_demo", correlation_id=cid) as t:
            _ = sum(range(1000))
        print(f"\n→ kbchat.observability 可用，cid={cid}, dur={t.duration_ms}ms")
    except Exception as e:
        print(f"\n⚠ kbchat 不可用：{e}")

    (ART / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, indent=2), encoding="utf-8")
    (ART / "report.md").write_text(
        "# L14 — Observability 报告\n\n"
        "## 关键指标\n"
        "- **p50/p95 延迟**（按 stage 拆分）\n"
        "- **错误率**（按 error 类型）\n"
        "- **trace 关联**（correlation_id 串联）\n\n"
        "## kbchat 隐私策略\n"
        "- trace **绝不记录** chunk 文本或 query 内容\n"
        "- 仅记录 stage / duration / 错误类型 / doc_id 数量\n\n"
        "## 面试要点\n"
        "1. 链路追踪：OpenTelemetry / LangSmith / Langfuse\n"
        "2. 采样策略：100% → 10% → 1%\n"
        "3. 隐私红线：PII 不能进 trace\n"
        "4. 告警阈值：p95 > 2s, error_rate > 5%\n",
        encoding="utf-8",
    )
    print(f"\n✓ L14 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
