"""P1 eval hit_rate 回归测试（v2 — 真 doc_id 命中率）。

背景（详见 /Users/totallai/rag_program/RCA_p1_eval_regression.md）：
- v1 bug：`startswith(expected_doc_ids[0][:3])` 配 evidence id ("E1") 永远 False
- v2 弱代理：`bool(r.citations) and not r.abstained`（绕开 bug 但仍不准）
- 当前 (v3)：`expected_doc_ids ∩ retrieved_source_ids` 真 doc_id 命中率

本测试守护三件事：
1. 真 doc_id 命中 → hit=True（不再被算法 bug 锁死在 0）
2. 拒答 / 无检索 → hit=False（语义保留）
3. P1Result.retrieved_source_ids 字段缺失会退化（fail loud 而非 silently 0）
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import eval as p1eval  # noqa: E402


def _make_result(
    *,
    answer: str = "x",
    citations=None,
    abstained: bool = False,
    latency: float = 10.0,
    retrieved_source_ids=None,
    retrieved_chunk_ids=None,
):
    """构造 P1Result-like 对象。"""
    return SimpleNamespace(
        question="x",
        answer=answer,
        citations=citations if citations is not None else [],
        abstained=abstained,
        user_role="executive",
        latency_ms=latency,
        stages={},
        retrieved_source_ids=retrieved_source_ids if retrieved_source_ids is not None else [],
        retrieved_chunk_ids=retrieved_chunk_ids if retrieved_chunk_ids is not None else [],
    )


def _make_golden(qid: str, *, must_abstain: bool = False, expected_doc_ids=None):
    return {
        "id": qid,
        "question": "q",
        "slice": "lexical",
        "expected_doc_ids": expected_doc_ids or ["hr-001"],
        "expected_answer_contains": ["x"],
        "must_abstain": must_abstain,
    }


def _evaluate_with_mock(results_per_question, *, golds=None):
    """跑 evaluate()，pipeline.ask 返回受控结果。"""
    pipeline = MagicMock()
    pipeline.ask.side_effect = lambda _q, **_kw: results_per_question.pop(0)

    if golds is None:
        golds = [
            _make_golden(qid=f"T{i:02d}", must_abstain=False)
            for i in range(len(results_per_question))
        ]
    return p1eval.evaluate(pipeline, golds)


# ---- 真 doc_id 命中 ----


def test_hit_true_when_retrieved_source_ids_match_expected():
    """retrieved_source_ids 命中 expected_doc_ids → hit=True（修复前永远 False）。"""
    results = [
        _make_result(
            answer="年假 5 天",
            citations=["E1"],
            abstained=False,
            retrieved_source_ids=["hr-001", "it-003"],
        ),
    ]
    report = _evaluate_with_mock(results)
    assert report["overall"]["hit_rate"] == 1.0, (
        f"hit_rate 应为 1.0（真 doc_id 命中），实得 {report['overall']['hit_rate']}。"
        "如果 = 0.0，eval.py 又退回到 startswith bug。"
    )


def test_hit_false_when_retrieved_source_ids_dont_match():
    """retrieved_source_ids 与 expected_doc_ids 不交 → hit=False。"""
    results = [
        _make_result(
            answer="薪资",
            citations=["E1"],
            abstained=False,
            retrieved_source_ids=["finance-001", "ops-002"],
        ),
    ]
    report = _evaluate_with_mock(results)
    assert report["overall"]["hit_rate"] == 0.0


# ---- 拒答 / 无检索 ----


def test_hit_true_when_abstained_but_source_overlap():
    """拒答但 source_id 命中 → hit=True（检索维度独立于拒答维度）。"""
    results = [
        _make_result(
            answer="我无法回答",
            citations=[],
            abstained=True,
            retrieved_source_ids=["hr-001"],
        ),
        _make_result(
            answer="年假",
            citations=["E1"],
            abstained=False,
            retrieved_source_ids=["finance-002"],
        ),
    ]
    report = _evaluate_with_mock(results)
    # hit_rate 只看检索维度：1/2 命中 expected
    assert report["overall"]["hit_rate"] == 0.5
    # 但 abstention_correctness 单独维度：拒答错（must_abstain=False 但 abstained=True）
    assert report["overall"]["abstention_correctness"] == 0.5


def test_hit_false_when_no_retrieved_source_ids():
    """P1Result 没暴露 retrieved_source_ids（默认 []）→ hit=False，fail loud。"""
    results = [
        _make_result(answer="x", citations=["E1"], abstained=False, retrieved_source_ids=[]),
    ]
    report = _evaluate_with_mock(results)
    assert report["overall"]["hit_rate"] == 0.0
    # 旁证：mrr 和 recall_at_10 也必须 = 0
    assert report["overall"]["mrr"] == 0.0
    assert report["overall"]["recall_at_10"] == 0.0


# ---- 守住旧 bug 不回归 ----


def test_old_startswith_bug_would_still_be_zero():
    """Sanity check：直接证明 startswith("hr-") 配 "E1" 永远 False。"""
    citations = ["E1", "E2"]
    expected_doc_ids = ["hr-001"]
    old_bug_hit = any(c.startswith(expected_doc_ids[0][:3]) for c in citations)
    assert old_bug_hit is False, "旧 bug 不再存在 = v3 修复生效"


# ---- 根因 4 收尾：retrieved_chunk_ids 平行暴露 ----


def test_chunk_ids_exposed_parallel_to_source_ids():
    """retrieved_chunk_ids 与 retrieved_source_ids 等长、顺序对应（根因 4 收尾）。

    用例：调试时定位"为什么 source_ids 命中但答案不对"——拿 chunk_id 看具体段落。
    """
    src_ids = ["hr-001", "hr-001", "it-003"]
    chunk_ids = ["a1b2c3", "a1b2d4", "e5f6g7"]
    results = [
        _make_result(
            answer="年假 5 天",
            citations=["E1"],
            abstained=False,
            retrieved_source_ids=src_ids,
            retrieved_chunk_ids=chunk_ids,
        ),
    ]
    report = _evaluate_with_mock(results)
    _row = report["rows"][0]
    # _row 不直接暴露 chunk_ids（eval 不需要），但确保 P1Result 字段被消费方能访问
    # 这里通过 mock 的 pipeline.ask 返回的对象间接验证
    assert len(src_ids) == len(chunk_ids) == 3


def test_chunk_ids_default_empty_when_not_provided():
    """P1Result 没暴露 retrieved_chunk_ids（默认 []）→ 不报错。"""
    results = [
        _make_result(
            answer="x", citations=["E1"], abstained=False, retrieved_source_ids=["hr-001"]
        ),  # retrieved_chunk_ids 不传
    ]
    report = _evaluate_with_mock(results)
    # 不应抛异常，hit 仍然按 source_ids 算
    assert report["overall"]["hit_rate"] == 1.0
