"""P1 — C1 真 RAGAS 指标测试 (ENTERPRISE_AUDIT C1).

覆盖:
- retrieval.ndcg_at_k 边界 (empty / perfect / partial / oversized)
- MmxRagasLLM mmx 调用约定 (subprocess mocked)
- metrics.faithfulness / answer_relevancy
- hallucination.* 与 numeric_hallucination_rate
- runner.py 入口 (smoke)
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

# 顶层 eval/ragas/ 包与 P1 src/eval.py 同名;P1 conftest 把 src 加到
# sys.path[0],会让 `import eval` 直接解析成 P1 模块。我们构造一个合成包
# `tests._ragas_v` 并把 ragas/* 作为子模块加载,这样内部 relative imports
# (`from .ragas_llm import ...`) 能正常解析。
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RAGAS_PKG = _REPO_ROOT / "eval" / "ragas"


def _build_ragas_pkg():
    pkg_name = "tests_ragas_v"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_RAGAS_PKG)]
    sys.modules[pkg_name] = pkg
    # 1) 先把每个子模块挂上 (这样 __init__ 里 `from .ragas_llm import ...` 能找到)
    for fname in ("ragas_llm", "retrieval", "metrics", "hallucination", "runner", "compare"):
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.{fname}", _RAGAS_PKG / f"{fname}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = pkg_name
        sys.modules[f"{pkg_name}.{fname}"] = mod
        spec.loader.exec_module(mod)
        pkg.__dict__[fname] = mod
        setattr(sys.modules[pkg_name], fname, mod)
    # 2) 再 exec 原 __init__.py (里面的 `from . import runner, compare` 现在能解析)
    init_path = _RAGAS_PKG / "__init__.py"
    init_src = init_path.read_text(encoding="utf-8")
    exec(compile(init_src, str(init_path), "exec"), pkg.__dict__)
    return pkg


_ragas_pkg = _build_ragas_pkg()


def _pkg_attr(name: str):
    """从合成包里取子模块引用(useful for tests)。"""
    return getattr(_ragas_pkg, name)


# 兼容旧名
_retrieval = _pkg_attr("retrieval")
_llm = _pkg_attr("ragas_llm")
_metrics = _pkg_attr("metrics")
_hallu = _pkg_attr("hallucination")
_runner = _pkg_attr("runner")
_compare = _pkg_attr("compare")


# 1. nDCG@k


class TestNdcg:
    def test_empty_retrieved_returns_zero(self):
        assert _retrieval.ndcg_at_k([], ["hr-001"], k=5) == 0.0

    def test_empty_expected_returns_zero(self):
        assert _retrieval.ndcg_at_k(["hr-001"], [], k=5) == 0.0

    def test_perfect_rank_at_5(self):
        score = _retrieval.ndcg_at_k(["hr-001", "hr-002", "hr-003"], ["hr-001"], k=5)
        assert score == 1.0

    def test_partial_overlap(self):
        score = _retrieval.ndcg_at_k(["hr-001", "other"], ["hr-001", "hr-002"], k=5)
        assert 0.5 < score < 0.7

    def test_no_match_returns_zero(self):
        assert _retrieval.ndcg_at_k(["other-1", "other-2"], ["hr-001", "hr-002"], k=5) == 0.0

    def test_rank_beyond_k_returns_zero(self):
        retrieved = [f"x{i}" for i in range(10)] + ["hr-001"]
        assert _retrieval.ndcg_at_5(retrieved, ["hr-001"]) == 0.0
        assert _retrieval.ndcg_at_10(retrieved, ["hr-001"]) == 0.0

    def test_k_truncation_rank_within(self):
        # rank 1 命中,无论 k=5/10 都是 1.0
        retrieved = ["hr-001"] + [f"x{i}" for i in range(20)]
        assert _retrieval.ndcg_at_5(retrieved, ["hr-001"]) == 1.0
        assert _retrieval.ndcg_at_10(retrieved, ["hr-001"]) == 1.0


# 2. MmxRagasLLM


class TestMmxRagasLLM:
    def test_generate_text_parses_anthropic_shape(self):
        fake_stdout = json.dumps({"content": [{"text": "5"}], "role": "assistant"})
        llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=fake_stdout, stderr=""
            )
            out = llm.generate_text("score 0-5")
        assert out == "5"
        cmd = run.call_args[0][0]
        assert "text" in cmd and "chat" in cmd
        assert "--model" in cmd
        assert "--quiet" in cmd

    def test_generate_text_handles_timeout(self):
        llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo", timeout=1)
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="mmx", timeout=1),
        ):
            with pytest.raises(RuntimeError, match="timeout"):
                llm.generate_text("x")

    def test_agenerate_text_returns_same_as_sync(self):
        import asyncio

        llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"text": "good"}),
                stderr="",
            )
            sync = llm.generate_text("x")
            async_result = asyncio.run(llm.agenerate_text("x"))
        assert sync == "good"
        assert async_result == "good"


# 3. RAGAS-style 指标 (LLM mocked)


class TestFaithfulness:
    def test_full_support(self):
        answer = "员工年假 5 天。病假 3 天。"
        contexts = ["员工年假 5 天", "病假 3 天"]
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"text": "1"}), stderr=""
            )
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            score = _metrics.faithfulness(answer, contexts, llm)
        assert score == 1.0

    def test_half_support(self):
        # 2 个 statement,LLM 依次返回 "1" 和 "0" → 0.5
        seq = ["1", "0"]
        it = iter(seq)
        with patch("subprocess.run") as run:

            def _stub(*a, **kw):
                r = next(it)
                return SimpleNamespace(
                    returncode=0, stdout=json.dumps({"text": r}), stderr=""
                )

            run.side_effect = _stub
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            score = _metrics.faithfulness("声明 1。声明 2。", ["仅支持声明 1"], llm)
        assert score == 0.5

    def test_empty_answer_returns_zero(self):
        llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
        with patch("subprocess.run"):
            assert _metrics.faithfulness("", ["ctx"], llm) == 0.0


class TestAnswerRelevancy:
    def test_relevancy_normalizes_5_to_1(self):
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"text": "5"}), stderr=""
            )
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            score = _metrics.answer_relevancy("q", "答对问题", ["ctx"], llm)
        assert score == 1.0

    def test_relevancy_parses_score_3(self):
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"text": "3"}), stderr=""
            )
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            score = _metrics.answer_relevancy("q", "答", ["ctx"], llm)
        # 3/5 = 0.6
        assert 0.5 < score < 0.7


# 4. Hallucination


class TestHallucination:
    def test_hallucination_rate_is_inverse_of_faithfulness(self):
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"text": "1"}), stderr=""
            )
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            f = _metrics.faithfulness("答", ["证据"], llm)
            h = _hallu.hallucination_rate("答", ["证据"], llm)
        assert f + h == 1.0

    def test_numeric_zero_when_all_in_context(self):
        score = _hallu.numeric_hallucination_rate("员工年假 5 天", ["年假 5 天"])
        assert score == 0.0

    def test_numeric_full_when_all_made_up(self):
        score = _hallu.numeric_hallucination_rate("100 和 200", ["无关"])
        assert score == 1.0

    def test_unsupported_claims_parses_count(self):
        fake_output = "- claim 1\n- claim 2\n- claim 3\nunsupported: 2"
        with patch("subprocess.run") as run:
            run.return_value = SimpleNamespace(
                returncode=0, stdout=json.dumps({"text": fake_output}), stderr=""
            )
            llm = _llm.MmxRagasLLM(mmx_bin="/usr/bin/echo")
            score = _hallu.unsupported_claims_rate("claim 1", ["ctx"], llm)
        # 3 个声明,2 个 unsupported → 2/3 ≈ 0.667
        assert 0.6 < score < 0.7


# 5. runner 入口 (import smoke)


class TestRunnerImport:
    def test_runner_module_surface(self):
        assert hasattr(_runner, "main")
        assert hasattr(_runner, "evaluate")
        assert hasattr(_runner, "RagasReport")
        assert hasattr(_runner, "RagasRow")

    def test_supported_metrics_are_four_ragas_core(self):
        assert set(_runner.SUPPORTED_METRICS.keys()) == {
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        }


# 6. compare harness


class TestCompareHarness:
    def test_compare_report_constructor(self):
        rep = _compare.CompareReport(
            a_label="a.yml", b_label="b.yml", n=10,
            metrics_a={"hit_rate": 0.4, "p50_latency_ms": 5000},
            metrics_b={"hit_rate": 0.6, "p50_latency_ms": 4000},
            delta={},
            timestamp="2026-01-01T00:00:00Z",
        )
        assert rep.n == 10
        assert rep.metrics_a["hit_rate"] == 0.4
        assert rep.metrics_b["p50_latency_ms"] == 4000

    def test_render_markdown_contains_metric_table(self):
        rep = _compare.CompareReport(
            a_label="a", b_label="b", n=5,
            metrics_a={"hit_rate": 0.5},
            metrics_b={"hit_rate": 0.7},
            delta={"hit_rate": {"a": 0.5, "b": 0.7, "delta": 0.2, "better": "b"}},
            timestamp="2026-07-28T12:00:00Z",
        )
        md = _compare._render_markdown(rep)
        assert "hit_rate" in md
        assert "0.5" in md and "0.7" in md
        assert "b" in md
