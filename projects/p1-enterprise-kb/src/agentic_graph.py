"""P1 — Agentic RAG 包装层(真 LangGraph)。

为什么不直接重写 P1Pipeline.ask():
- pipeline.py 是稳定的自包含路径(50 题 golden + verify-p1 测它)
- agentic_graph.py 是叠加层: 跑同一 P1Pipeline,加 Self-RAG 反思 + CRAG 改写循环
- 不动 pipeline.py / app.py / 既有 tests,只新增这个文件和它的测试

Agentic 流程(LangGraph StateGraph):
    ┌──────┐  grade=poor  ┌─────────┐
    │retrieve├─────────────►│ rewrite │
    └──┬────┘              └────┬────┘
       │ grade=good             │
       ▼                        ▼ (回边)
    ┌──────┐
    │ done │ ◄──────────────── retrieve
    └──────┘

节点:
- retrieve: 调 P1Pipeline.ask() 拿 P1Result,grade 评估
- rewrite:  启发式简化问题(去问号/语气词),作为下一轮 retrieve 的 query
- done:     选 abstained=False 且 citations 最多的 attempt 作为 final_answer

跑:
    python projects/p1-enterprise-kb/src/test_agentic_graph.py
"""

from __future__ import annotations

from typing import TypedDict

import llm_judge  # noqa: E402
from langgraph.graph import END, START, StateGraph

# pipeline.py 内部 sys.path 注入,这里直接 from
from pipeline import P1Pipeline, P1Result  # noqa: E402


class AgentState(TypedDict, total=False):
    """LangGraph 共享 state。

    注:`pipeline` 是 P1Pipeline 实例,只放在内存中,不可序列化。
    LangGraph 默认不 checkpoint,放 state 里没问题;
    真要 checkpoint 时需要排除非 serializable 字段。
    """

    pipeline: P1Pipeline
    question: str
    rewritten_question: str
    attempts: list[dict]  # 历次 retrieve 的 P1Result.__dict__
    grade: str  # "good" / "poor"
    retries: int
    final_answer: str
    log: list[dict]


def _grade(result: P1Result, question: str) -> str:
    """Self-RAG LLM-judge 版 grade(取代之前的启发式)。

    策略: 始终把 answer 当证据让 LLM judge 评「能否回答问题」。
    - LLM 拒答("我无法在...找到相关信息")→ judge 应判 poor
    - LLM 答出具体事实("年假 5 天")→ judge 应判 good
    - mmx 不可用 → fallback 启发式(看 citations + abstained)

    Args:
        result: P1Pipeline._ask_legacy 返回的 P1Result
        question: 用户原始问题

    Returns:
        "good" / "poor"
    """
    # 用 answer 当 evidence(LLM 拒答信号也在 answer 里)
    evidence_text = (result.answer or "").strip()[:1500]
    if not evidence_text:
        # answer 为空 → 必 poor
        return "poor"
    try:
        return llm_judge.llm_grade(question, evidence_text)
    except Exception:
        # LLM 不可用 → 启发式 fallback
        return llm_judge.heuristic_grade_for(question, result.citations, result.abstained)


def _heuristic_rewrite(q: str) -> str:
    """CRAG 改写简化版:不调 LLM,纯规则缩短问题。

    去掉「怎么办/怎么/吗」等口语词,去问号,截到前 12 字。
    失败兜底: 原 query 前 8 字。
    """
    cleaned = q
    for fluff in ("怎么办", "怎么", "吗", "我想知道", "请问", "请告诉我"):
        cleaned = cleaned.replace(fluff, "")
    cleaned = cleaned.replace("?", "").replace("?", "").strip()
    return (cleaned or q)[:12]


def _node_retrieve(state: AgentState) -> dict:
    """retrieve 节点: 调 P1Pipeline._ask_legacy()(不走 graph,避免递归)。

    注意:不能用 pipe.ask() —— P1Pipeline.ask() 已切换到 ask_agentic()(本 graph),
    否则会无限递归。
    """
    pipe: P1Pipeline = state["pipeline"]
    q = state.get("rewritten_question") or state["question"]
    r: P1Result = pipe._ask_legacy(q)
    grade = _grade(r, state["question"])  # ← 用 LLM judge 取代启发式
    attempt = {
        "question": q,
        "answer": r.answer,
        "citations": r.citations,
        "abstained": r.abstained,
        "latency_ms": round(r.latency_ms, 1),
        "user_role": r.user_role,
        "stages": r.stages,
        "retrieved_source_ids": r.retrieved_source_ids,
        "retrieved_chunk_ids": r.retrieved_chunk_ids,
    }
    return {
        "attempts": state.get("attempts", []) + [attempt],
        "grade": grade,
        "log": state.get("log", [])
        + [
            {
                "step": "retrieve",
                "q": q,
                "grade": grade,
                "abstained": r.abstained,
                "citations": len(r.citations),
                "latency_ms": round(r.latency_ms, 1),
            }
        ],
    }


def _node_rewrite(state: AgentState) -> dict:
    """rewrite 节点: 启发式简化问题,供下一轮 retrieve 使用。"""
    new_q = _heuristic_rewrite(state["question"])
    return {
        "rewritten_question": new_q,
        "retries": state.get("retries", 0) + 1,
        "log": state.get("log", [])
        + [
            {
                "step": "rewrite",
                "new_q": new_q,
                "retry": state["retries"] + 1,
            }
        ],
    }


def _node_done(state: AgentState) -> dict:
    """done 节点: 选 best attempt 作为 final_answer。

    选 best 标准: 优先 abstained=False 且 citations 最多的;
    都没有就退回到最后一次 attempt。
    """
    best = None
    for a in state.get("attempts", []):
        if a.get("abstained"):
            continue
        if best is None or len(a.get("citations", [])) > len(best.get("citations", [])):
            best = a
    if best is None and state.get("attempts"):
        best = state["attempts"][-1]
    final = (best or {}).get("answer", "（无答案 — graph 异常）")
    return {"final_answer": final}


def _build_graph(max_retries: int = 2):
    """编译 LangGraph;max_retries 通过 closure 注入 route 函数。"""
    g = StateGraph(AgentState)
    g.add_node("retrieve", _node_retrieve)
    g.add_node("rewrite", _node_rewrite)
    g.add_node("done", _node_done)

    g.add_edge(START, "retrieve")  # 入口
    g.add_conditional_edges(  # 条件边
        "retrieve",
        lambda s: (
            "rewrite" if (s["grade"] == "poor" and s.get("retries", 0) < max_retries) else "done"
        ),
        {"rewrite": "rewrite", "done": "done"},
    )
    g.add_edge("rewrite", "retrieve")  # 回边: 改写后重检
    g.add_edge("done", END)  # 出口
    return g.compile()


def ask_agentic(pipeline: P1Pipeline, question: str, max_retries: int = 2) -> dict:
    """主入口: 跑 LangGraph 包装的 agentic RAG。

    Args:
        pipeline: 已 index_corpus() 的 P1Pipeline 实例
        question: 用户问题
        max_retries: 最多重试改写次数,默认 2

    Returns:
        {
          "final_answer": str,   # 选 best attempt 的 answer
          "attempts":     list,  # 历次 P1Result.__dict__
          "log":          list,  # 每步 step + grade + latency
        }
    """
    app = _build_graph(max_retries=max_retries)
    final = app.invoke(
        {
            "pipeline": pipeline,
            "question": question,
            "retries": 0,
            "attempts": [],
            "log": [],
        }
    )
    return {
        "final_answer": final.get("final_answer", ""),
        "attempts": final.get("attempts", []),
        "log": final.get("log", []),
    }


# ── CLI 演示: 3 题对比 P1Pipeline.ask() vs ask_agentic ─────────
def _cli_compare():
    """打印 LangGraph vs 单次 P1Pipeline 对比;不入仓,只给人工看效果。"""
    from acl import User

    user = User(user_id="alice", role="manager")
    pipe = P1Pipeline(user=user)
    pipe.index_corpus()
    print(f"→ 索引: {len(pipe.index)} chunks (role={user.role})\n")

    for q in ["员工每年能休多少天年假?", "P0 事故 30 分钟内如何处理?", "限制性股票 vest 政策?"]:
        print(f"━━━ Q: {q} ━━━")
        # 1) 单次 baseline(走 _ask_legacy 避免递归)
        r1 = pipe._ask_legacy(q)
        print(f"  baseline: {r1.answer[:80]} | 引用 {r1.citations} | 拒答 {r1.abstained}")
        # 2) Agentic —— ask() 走 ask_agentic → graph → retrieve 调 _ask_legacy
        r2 = pipe.ask(q)
        print(
            f"  agentic (via ask): {r2.answer[:80]} | 引用 {r2.citations} | 拒答 {r2.abstained} | attempts {r2.stages.get('attempts')}"
        )
        print()


if __name__ == "__main__":
    _cli_compare()
