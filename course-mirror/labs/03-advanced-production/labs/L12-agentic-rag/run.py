"""L12 — Agentic RAG：用 LangGraph 实现多步检索 + Self-RAG 反思 + CRAG 修正 + 多轮记忆。

跑：
    python 03-advanced-production/labs/L12-agentic-rag/run.py

真 LangGraph 实现：3 节点 StateGraph + MemorySaver 多轮
- retrieve: 检索（含历史上下文）
- grade: 评估证据是否够好（Self-RAG 思想，由 retrieve 节点内一并完成）
- rewrite: 不够好则改写 query 再检索（CRAG 思想；条件边 + 回边）
- generate: 最终生成（append AIMessage 到 messages）
- chat(): 多轮入口，thread_id 用 MemorySaver 持久化 state

新能力（vs 单轮）：
- messages 字段用 add_messages reducer，自动累积多轮对话
- retrieve 节点读取 history 让 query 带上下文
- MemorySaver 让 thread 可暂停/恢复（demo 用 in-memory，生产换 PostgresSaver）
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

ART = Path("artifacts/L12")
ART.mkdir(parents=True, exist_ok=True)


# ── 1. Toy retriever（同 L03 的简化版）─────────────────
import numpy as np

CORPUS = [
    "员工年假政策：司龄 1 年以下 5 天，1-3 年 10 天，3-5 年 15 天。",
    "病假需要 OA 申请，附医院证明。",
    "差旅住宿：一线城市 800 元，二线 600 元。",
    "VPN 接入：vpn.corp.example.com。",
    "限制性股票 4 年 vest，cliff 1 年。",
    "P0 事故 30 分钟内 mitigation。",
    "发版：周二封板、周三灰度 10%、周五 100%。",
    "用户 P0 反馈 1 小时响应。",
]


def toy_embed(text: str) -> np.ndarray:
    """极简 embedding：词袋。"""
    v = np.zeros(64, dtype=np.float32)
    for i, ch in enumerate(text):
        v[hash(ch) % 64] += 1
    return v / max(np.linalg.norm(v), 1e-9)


DOC_VECS = [toy_embed(d) for d in CORPUS]


def retrieve(query: str, k: int = 2) -> list[dict]:
    qv = toy_embed(query)
    sims = [(i, float(dv @ qv)) for i, dv in enumerate(DOC_VECS)]
    sims.sort(key=lambda x: -x[1])
    return [{"doc_id": f"D{i}", "text": CORPUS[i], "score": s} for i, s in sims[:k]]


# ── 2. Self-RAG 评分:证据够不够(用 LLM judge) ─────────
def grade_evidence(question: str, evidence: list[dict]) -> dict:
    """LLM-judge grade —— 复用 P1 的 llm_judge 实现,失败 fallback 关键词重叠。

    返回 {"grade": "good" | "poor", "score": float, "judge": str}。
    - judge="llm"  → LLM 评的(主路径)
    - judge="heuristic"  → LLM 不可用,降级到关键词重叠
    """
    # LLM judge 主路径:复用 P1 的 llm_judge(避免重复实现)
    try:
        import sys
        from pathlib import Path
        p1_src = Path(__file__).resolve().parents[3] / "projects" / "p1-enterprise-kb" / "src"
        if str(p1_src) not in sys.path:
            sys.path.insert(0, str(p1_src))
        from llm_judge import llm_grade
        evidence_text = "\n".join(e.get("text", "")[:400] for e in evidence)
        if not evidence_text.strip():
            return {"grade": "poor", "score": 0.0, "judge": "heuristic"}
        g = llm_grade(question, evidence_text)
        return {"grade": g, "score": 1.0 if g == "good" else 0.0, "judge": "llm"}
    except Exception:
        pass
    # Fallback: 关键词重叠(老实现)
    q_words = set(question)
    if not evidence:
        return {"grade": "poor", "score": 0.0, "judge": "heuristic"}
    matched = sum(1 for e in evidence if any(w in e["text"] for w in q_words if len(w) > 1))
    score = matched / max(len(evidence), 1)
    return {"grade": "good" if score > 0.5 else "poor", "score": round(score, 2), "judge": "heuristic"}


# ── 3. CRAG 改写：poor 时让 LLM 改写 query ────────────
def rewrite_query(question: str) -> str:
    """改写成更短或更具体的查询。"""
    # 简化：用 LLM 改写
    if os.environ.get("RAG_PROG_LLM_BACKEND", "mmx") == "mmx":
        import subprocess
        try:
            r = subprocess.run(
                ["mmx", "text", "chat", "--model", "MiniMax-M3",
                 "--max-tokens", "64", "--temperature", "0.0", "--output", "json",
                 "--system", "你是查询改写助手。把用户问题改写得更具体、更短。",
                 "--message", f"原问题：{question}\n改写："],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0:
                return json.loads(r.stdout).get("text", question).strip()
        except Exception:
            pass
    return question  # fallback


# ── 4. 真 LangGraph StateGraph ──────────────────────
class State(TypedDict, total=False):
    """LangGraph 共享 state。

    `messages` 用 add_messages reducer,每节点返回 dict 都按 append 合并,
    这是 LangGraph 跨 invoke 累积多轮对话的关键(否则会被 replace 覆盖)。
    """
    messages: Annotated[list, add_messages]    # 多轮对话,HumanMessage/AIMessage dict
    question: str                               # 本轮原始问题
    evidence: list[dict]
    grade: str
    rewritten_question: str
    retries: int
    answer: str
    log: list[dict]


def _node_retrieve(state: State) -> dict:
    """retrieve 节点: 检索 + grade 一并完成(简化版 Self-RAG)。

    多轮增强: 若 messages 里有上一轮 user,把上一轮 question 拼到当前 query 前,
    让「那病假呢?」这种 follow-up 能继承「年假」的上下文。
    """
    q = state.get("rewritten_question") or state["question"]
    # 历史上下文(简化: 取上一轮 user message)
    history = state.get("messages", [])
    # add_messages reducer 把 dict 转成 HumanMessage/AIMessage 对象,所以用 getattr 而非 .get
    prior_user_qs = [m.content for m in history if getattr(m, "role", None) == "user"]
    if len(prior_user_qs) >= 2:  # 至少有一轮历史
        ctx_q = prior_user_qs[-2]  # 上一轮 user
        full_q = f"{ctx_q} | {q}"
    else:
        full_q = q
    evidence = retrieve(full_q, k=2)
    grade = grade_evidence(state["question"], evidence)["grade"]
    return {
        "evidence": evidence,
        "grade": grade,
        "log": state.get("log", []) + [{
            "step": "retrieve", "q": full_q,
            "evidence_count": len(evidence), "grade": grade,
            "with_history": len(prior_user_qs) >= 2,
        }],
    }


def _node_rewrite(state: State) -> dict:
    """rewrite 节点: 改写原 question 为更短/更具体的查询,作为下一轮 retrieve 的输入。"""
    new_q = rewrite_query(state["question"])
    return {
        "rewritten_question": new_q,
        "retries": state.get("retries", 0) + 1,
        "log": state.get("log", []) + [{
            "step": "rewrite", "new_q": new_q,
            "retry": state["retries"] + 1,
        }],
    }


def _node_generate(state: State) -> dict:
    """generate 节点: 简化版拼接 evidence 作为最终答案,同时 append AIMessage。"""
    ans = " | ".join(e["text"][:50] for e in state["evidence"][:2])
    # 关键: 返回 messages 时 add_messages reducer 自动 append(不会覆盖历史)
    return {
        "answer": ans,
        "messages": [{"role": "assistant", "content": ans}],
    }


def _build_graph(max_retries: int = 2, with_checkpointer: bool = False):
    """编译 LangGraph;max_retries 通过 closure 注入 route 函数。

    with_checkpointer=True 时挂上 MemorySaver,支持多轮 + 暂停/恢复。
    """
    g = StateGraph(State)
    g.add_node("retrieve", _node_retrieve)
    g.add_node("rewrite", _node_rewrite)
    g.add_node("generate", _node_generate)

    g.add_edge(START, "retrieve")                           # 入口
    g.add_conditional_edges(                                # 条件边
        "retrieve",
        lambda s: "rewrite" if (s["grade"] == "poor" and s.get("retries", 0) < max_retries) else "generate",
        {"rewrite": "rewrite", "generate": "generate"},
    )
    g.add_edge("rewrite", "retrieve")                       # 回边: 改写后重检
    g.add_edge("generate", END)                             # 出口

    checkpointer = MemorySaver() if with_checkpointer else None
    return g.compile(checkpointer=checkpointer)


def agentic_rag(question: str, max_retries: int = 2) -> dict:
    """单轮入口(原 API 兼容);CompiledGraph.invoke 拿最终 state。"""
    app = _build_graph(max_retries=max_retries, with_checkpointer=False)
    final_state = app.invoke({
        "question": question,
        "messages": [{"role": "user", "content": question}],
        "retries": 0,
        "log": [],
    })
    return {"state": final_state, "log": final_state.get("log", [])}


# ── 多轮对话入口 ─────────────────────────────────────
_chat_app = None  # 全局 lazy-init(checkpointer 必须持久才能跨调用累积)


def _get_chat_app(max_retries: int = 2):
    """获取挂 MemorySaver 的 chat app(单例)。"""
    global _chat_app
    if _chat_app is None:
        _chat_app = _build_graph(max_retries=max_retries, with_checkpointer=True)
    return _chat_app


def chat(question: str, thread_id: str = "default", max_retries: int = 2) -> dict:
    """多轮对话入口。

    用 thread_id 把同一会话的 state 持久到 MemorySaver;
    同一 thread_id 的多次 invoke 会自动累积 messages(由 add_messages reducer 处理)。

    Args:
        question:   当前轮 user 输入
        thread_id:  会话标识;同 thread_id 累积历史,不同 thread 互相隔离
        max_retries: 最大改写重试次数

    Returns:
        {"state": ..., "log": ..., "thread_id": ...}
    """
    app = _get_chat_app(max_retries=max_retries)
    cfg = {"configurable": {"thread_id": thread_id}}
    final_state = app.invoke(
        {
            "question": question,
            "messages": [{"role": "user", "content": question}],
            "retries": 0,
            "log": [],
        },
        config=cfg,
    )
    return {"state": final_state, "log": final_state.get("log", []), "thread_id": thread_id}


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L12 — Agentic RAG（LangGraph 真实装 + 多轮记忆）")
    print("════════════════════════════════════════════\n")

    # 演示 1：单轮（API 兼容）
    queries = ["我想休年假", "P0 事故 怎么办", "限制性股票 怎么 vest"]
    results = []
    for q in queries:
        print(f"\n━━━ Q (单轮): {q} ━━━")
        out = agentic_rag(q, max_retries=2)
        for step in out["log"]:
            print(f"  {step}")
        print(f"  → answer: {out['state']['answer']}")
        results.append({"q": q, "log": out["log"], "answer": out["state"]["answer"]})

    # 演示 2：多轮（thread_id 持久化 + history 上下文传播）
    print("\n━━━ 多轮对话（同一 thread_id,MemorySaver 持久化）━━━\n")
    thread = "alice"
    history_log: list[dict] = []
    for q in ["年假几天?", "那病假呢?", "差旅能报多少?"]:
        out = chat(q, thread_id=thread, max_retries=2)
        msgs = out["state"].get("messages", [])
        print(f"  [turn {len(history_log) + 1}] user: {q}")
        print(f"    msgs: {len(msgs)} 条 in state (累积中)")
        print(f"    → assistant: {out['state']['answer'][:80]}")
        history_log.append({"q": q, "log": out["log"], "answer": out["state"]["answer"]})

    # 演示 3：thread 隔离
    print("\n━━━ 新 thread_id (bob) 不应继承 alice 的历史 ━━━\n")
    out = chat("年假几天?", thread_id="bob", max_retries=2)
    msgs = out["state"].get("messages", [])
    print(f"  [bob turn 1] msgs: {len(msgs)} 条 (期望 2: user + assistant)")
    print(f"  → answer: {out['state']['answer'][:80]}")

    # 写结果
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "results.json").write_text(
        json.dumps({
            "single_turn": results,
            "multi_turn": history_log,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # LangGraph 已实装
    try:
        import langgraph
        from importlib.metadata import version
        print(f"\n✓ LangGraph StateGraph 已实装: 3 节点 + 条件边 + 回边 + MemorySaver "
              f"(langgraph {version('langgraph')})")
    except ImportError:
        print("\n⚠ langgraph 未安装,本 lab 不可运行")

    (ART / "report.md").write_text(
        """# L12 — Agentic RAG 报告

## Self-RAG vs CRAG vs 朴素 Agentic

| 模式 | 思路 | 适用 |
|---|---|---|
| Self-RAG | LLM 自评「证据够吗」 | 多步 QA、长答 |
| CRAG | 不够时改写 query 再检索 | 检索质量波动大 |
| 朴素 Agentic | 多步 tool use | 复杂多跳 |

## 本 lab 演示
1. retrieve → 2. grade → 3. if poor: rewrite + retrieve → 4. generate

## 真 LangGraph 实现要点
- **3 节点** StateGraph: `retrieve` / `rewrite` / `generate`
- **条件边**: `add_conditional_edges("retrieve", route_fn, {"rewrite": ..., "generate": ...})`
- **回边**: `add_edge("rewrite", "retrieve")` —— 这是 LangGraph 比 LCEL 强的关键(LCEL 没有循环)
- **多轮记忆**: `messages` 字段用 `add_messages` reducer 累积;`MemorySaver` + `thread_id` 跨 invoke 持久化
- **历史传播**: retrieve 节点把上一轮 user message 拼到当前 query,让「那病假呢?」继承「年假」上下文
- **可观测性**: `app.get_graph().draw_mermaid()` 出 Mermaid 图
- **生产级扩展**: `MemorySaver` → `PostgresSaver` 即得跨进程 checkpoint + human-in-the-loop

## 面试要点
1. **Self-RAG** 论文关键：reflection tokens
2. **CRAG** 用 Confidence 触发 3 个 action：Correct/Incorrect/Ambiguous
3. **Agentic** 风险：循环调用 → 需 max_retries + cost guard
4. **LangGraph vs LCEL**: 流程图能画出来 = 该用 LangGraph;线性管道 = LCEL 够
5. **MemorySaver**: thread_id 隔离,生产换 PostgresSaver;可用于人机协同(interrupt_before)
""",
        encoding="utf-8",
    )
    print(f"\n✓ L12 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
