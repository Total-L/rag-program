"""P1 eval 真指标（recall_at_10 / MRR）回归测试。

背景（详见 /Users/totallai/rag_program/RCA_p1_eval_regression.md 根因 7）：
- eval.py 文档头说会算 recall@k / MRR / faithfulness，实际只算了弱代理
- v3 (本文件) 用 retrieved_source_ids 做精确 doc_id 比对

本测试守护三个真指标：
1. recall_at_10 = |expected ∩ retrieved| / |expected|（命中比例）
2. mrr = 1 / 首次命中的排名（无命中 = 0）
3. faithfulness proxy = contains_expected
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


def _golden(qid: str, *, expected_doc_ids, expected_answer_contains=None):
    return {
        "id": qid,
        "question": "q",
        "slice": "lexical",
        "must_abstain": False,
        "expected_doc_ids": expected_doc_ids,
        "expected_answer_contains": expected_answer_contains or [],
    }


def _evaluate(golds, results):
    pipeline = MagicMock()
    pipeline.ask.side_effect = lambda _q, **_kw: results.pop(0)
    return p1eval.evaluate(pipeline, golds)


# ---- MRR 真指标 ----


def test_mrr_one_when_first_position_matches():
    """expected 在 retrieved 第 1 位 → MRR = 1.0"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["hr-001", "it-003"],
            answer="年假 5 天",
        )
    ]
    report = _evaluate(golds, results)
    assert report["rows"][0]["mrr"] == 1.0
    assert report["overall"]["mrr"] == 1.0


def test_mrr_half_when_second_position_matches():
    """expected 在 retrieved 第 2 位 → MRR = 0.5"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["it-003", "hr-001"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    assert report["rows"][0]["mrr"] == 0.5


def test_mrr_zero_when_no_match():
    """无 expected 命中 → MRR = 0.0"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["finance-001"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    assert report["rows"][0]["mrr"] == 0.0


def test_mrr_picks_first_matching_doc():
    """多个 expected 时 MRR 用第一个命中的排名"""
    golds = [_golden("T01", expected_doc_ids=["hr-001", "it-003"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["finance-001", "it-003", "hr-001"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    # 首次命中是 it-003（rank 2）→ MRR = 1/2 = 0.5
    assert report["rows"][0]["mrr"] == 0.5


# ---- recall@k 真指标 ----


def test_recall_full_match():
    """expected 全部在 retrieved → recall = 1.0"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["hr-001", "it-003"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    assert report["rows"][0]["recall_at_10"] == 1.0


def test_recall_partial_match():
    """expected 部分在 retrieved → recall = 命中比例"""
    golds = [_golden("T01", expected_doc_ids=["hr-001", "it-003"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["hr-001"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    # 命中 1 / expected 2 = 0.5
    assert report["rows"][0]["recall_at_10"] == 0.5


def test_recall_zero_when_no_match():
    """无 expected 命中 → recall = 0.0"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"])]
    results = [
        _make_result(
            abstained=False,
            retrieved_source_ids=["finance-001"],
            answer="x",
        )
    ]
    report = _evaluate(golds, results)
    assert report["rows"][0]["recall_at_10"] == 0.0


# ---- 聚合 ----


def test_overall_aggregates_mrr_correctly():
    """多题 MRR 算术平均"""
    golds = [
        _golden("T01", expected_doc_ids=["hr-001"]),
        _golden("T02", expected_doc_ids=["hr-001"]),
    ]
    results = [
        _make_result(retrieved_source_ids=["hr-001"]),  # MRR = 1.0
        _make_result(retrieved_source_ids=["it-003", "hr-001"]),  # MRR = 0.5
    ]
    report = _evaluate(golds, results)
    # (1.0 + 0.5) / 2 = 0.75
    assert report["overall"]["mrr"] == 0.75


def test_overall_aggregates_recall_correctly():
    """多题 recall 算术平均"""
    golds = [
        _golden("T01", expected_doc_ids=["hr-001"]),
        _golden("T02", expected_doc_ids=["hr-001", "it-003"]),
    ]
    results = [
        _make_result(retrieved_source_ids=["hr-001"]),  # recall = 1.0
        _make_result(retrieved_source_ids=["hr-001"]),  # recall = 1/2 = 0.5
    ]
    report = _evaluate(golds, results)
    assert report["overall"]["recall_at_10"] == 0.75


# ---- faith proxy ----


def test_contains_expected_pass():
    """答案含期望关键词 → contains_expected = True"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"], expected_answer_contains=["年假", "5 天"])]
    results = [_make_result(answer="员工年假是 5 天", retrieved_source_ids=["hr-001"])]
    report = _evaluate(golds, results)
    assert report["rows"][0]["contains_expected"] is True


def test_contains_expected_fail():
    """答案不含期望关键词 → contains_expected = False"""
    golds = [_golden("T01", expected_doc_ids=["hr-001"], expected_answer_contains=["年假"])]
    results = [_make_result(answer="薪资政策", retrieved_source_ids=["hr-001"])]
    report = _evaluate(golds, results)
    assert report["rows"][0]["contains_expected"] is False
