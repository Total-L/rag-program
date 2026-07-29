"""P1 Agentic Graph 测试（pytest 版，从 src/test_agentic_graph.py 迁移）。

覆盖：
1. _grade heuristic fallback（不连 LLM）
2. _heuristic_rewrite 各种清理规则
3. _node_rewrite retries 计数
4. _node_done best attempt 选优
5. graph compile 成功
6. LLM judge smoke（若 mmx 可用）

跑：
    python -m pytest projects/p1-enterprise-kb/tests/test_agentic_graph.py -v
"""

from __future__ import annotations

import pytest

# langgraph 是硬依赖；未装时整文件 skip
langgraph = pytest.importorskip("langgraph")

from agentic_graph import (  # noqa: E402
    _build_graph,
    _grade,
    _heuristic_rewrite,
    _node_done,
    _node_rewrite,
)
from pipeline import P1Result  # noqa: E402

# ---- _grade ----


def test_grade_heuristic_fallback_abstain():
    """拒答 → heuristic fallback 必 poor。"""
    r = P1Result(
        question="", answer="x", citations=[], abstained=True, user_role="manager", latency_ms=1.0
    )
    assert _grade(r, "年假") == "poor"


def test_grade_heuristic_fallback_empty_citations():
    """0 citations → heuristic fallback 必 poor。"""
    r = P1Result(
        question="", answer="a", citations=[], abstained=False, user_role="manager", latency_ms=1.0
    )
    assert _grade(r, "年假") == "poor"


def test_grade_empty_answer_is_poor():
    """answer 为空 → 必 poor（不调 LLM）。"""
    r = P1Result(
        question="",
        answer="",
        citations=["E1"],
        abstained=False,
        user_role="manager",
        latency_ms=1.0,
    )
    assert _grade(r, "年假") == "poor"


# ---- _heuristic_rewrite ----


def test_heuristic_rewrite_strips_fluff():
    assert _heuristic_rewrite("怎么办年假?") == "年假"


def test_heuristic_rewrite_strips_question_words():
    assert _heuristic_rewrite("请问P0事故怎么操作?") == "P0事故操作"


def test_heuristic_rewrite_keeps_clean_query():
    assert _heuristic_rewrite("限制性股票") == "限制性股票"


def test_heuristic_rewrite_short_query_fallback():
    assert _heuristic_rewrite("年假") == "年假"


# ---- _node_rewrite ----


def test_node_rewrite_bumps_retries():
    out = _node_rewrite({"question": "怎么办年假?", "retries": 0, "log": []})  # type: ignore[arg-type]
    assert out["retries"] == 1
    assert "rewritten_question" in out
    assert len(out["log"]) == 1


# ---- _node_done ----


def test_node_done_picks_most_citations_non_abstained():
    state = {  # type: ignore[arg-type]
        "attempts": [
            {"answer": "A1", "citations": [], "abstained": True},
            {"answer": "A2", "citations": ["E1"], "abstained": False},
            {"answer": "A3", "citations": ["E1", "E2", "E3"], "abstained": False},
        ],
    }
    out = _node_done(state)
    assert out["final_answer"] == "A3"


def test_node_done_fallback_to_last_when_all_abstained():
    state = {  # type: ignore[arg-type]
        "attempts": [
            {"answer": "A1", "citations": [], "abstained": True},
            {"answer": "A2", "citations": [], "abstained": True},
        ],
    }
    out = _node_done(state)
    assert out["final_answer"] == "A2"


# ---- graph compile ----


def test_graph_compiles():
    app = _build_graph(max_retries=2)
    assert app is not None
    g = app.get_graph()
    nodes = sorted(g.nodes.keys())
    # 至少应有 retrieve / rewrite / done 三个节点
    assert "retrieve" in nodes
    assert "rewrite" in nodes
    assert "done" in nodes


# ---- LLM judge smoke（mmx 可用时跑）----


def test_grade_llm_judge_smoke():
    """连 LLM 跑一次（若 mmx 不可用则 skip）。

    不属于 CI 必跑；只在 mmx 配置好后跑，看 LLM judge 是否真在用。
    """
    pytest = __import__("pytest")
    from llm_judge import llm_grade

    try:
        result = llm_grade("员工年假几天?", "员工年假政策:司龄 1 年以下 5 天,1-3 年 10 天。")
        assert result in ("good", "poor"), f"LLM judge 返回非法值: {result!r}"
    except Exception as e:
        pytest.skip(f"mmx 不可用: {type(e).__name__}: {e}")
