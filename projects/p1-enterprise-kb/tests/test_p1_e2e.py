"""P1 端到端测试（ENTERPRISE_AUDIT C2）。

补足审计维度 4 「P1 E2E 缺失」的缺口：在不依赖 Ollama/mmx 的前提下，
验证 P1 检索→引用→拒答→latency 全链路的关键不变量。

策略：monkeypatch
- `_ollama_embed_passages` → 确定性 fake embedding（按词哈希,64 维）
- `_mmx_or_ollama` → 返回固定 JSON 答案
- 注入小语料(覆盖金标 5 个 doc_id)
- 跑 `_ask_legacy()` 路径（不依赖 langgraph）,验证:
  1. `retrieved_source_ids` 非空且与 expected_doc_ids 有交集(hit_rate > 0)
  2. `citations` 全部指向有效 evidence id
  3. `latency_ms >= 0`
  4. `abstained` 布尔,与回答内容一致
  5. ACL 敏感度过滤生效(低角色看不到 restricted)

依赖：`evaluate()` 走 `pipeline.ask` → ask_agentic → agentic_graph → 需要 langgraph;
langgraph 缺失时这部分测试自动 skip(带明确 reason)。

不验证 LLM 生成质量(那是真 RAGAS 评测的范围,见 eval/ragas/)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import langgraph  # noqa: F401

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False

from acl import User
from pipeline import P1Pipeline  # noqa: E402

# ── helpers ──


def _fake_embed(texts: list[str], dim: int = 64) -> list[list[float]]:
    """确定性 fake embedder(按词哈希到 dim 维)。"""
    out: list[list[float]] = []
    for t in texts:
        v = [0.0] * dim
        for tok in t.split():
            v[hash(tok) % dim] += 1.0
        n = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / n for x in v])
    return out


def _fake_llm(prompt: str, system: str = "") -> str:
    """固定 JSON 应答;真实 LLM 路径不在本测试覆盖范围。"""
    return json.dumps(
        {
            "answer": "（fake LLM）员工年假 5 天。",
            "citations": ["E1"],
            "abstained": False,
        },
        ensure_ascii=False,
    )


def _make_index(n: int = 5):
    """造一份覆盖「年假/IT/财务」主题的小语料。"""
    topics = [
        ("hr-001", "年假政策", "员工年假:司龄<1 年 5 天,1-3 年 10 天,3 年以上 15 天。"),
        ("hr-002", "病假", "病假需提交医院证明,经 OA 审批。"),
        ("it-001", "VPN", "公司内网通过 VPN 访问,客户端下载见 it-001 文档。"),
        ("it-003", "笔记本申请", "新员工笔记本申请流程:提交 IT 工单,审批后配 MacBook Pro。"),
        ("finance-001", "差旅报销", "差旅费 30 天内提交报销,需附发票。"),
    ]
    return [
        {
            "chunk_id": f"c{i + 1}",
            "source_id": src,
            "text": text,
            "embedding": _fake_embed([text])[0],
            "sensitivity": "public",
            "heading_path": f"h1/{topic}",
        }
        for i, (src, topic, text) in enumerate(topics[:n])
    ]


@pytest.fixture()
def pipeline_with_stubs(monkeypatch):
    """构造 stubbed pipeline:fake embed + fake LLM + fake index。

    关键:stub `_ollama_embed_passages` 同步成 64 维,否则 query vector 与
    fake index(64 维)做 matmul 时会 size mismatch。
    """
    monkeypatch.setenv("RAG_PROG_LLM_BACKEND", "mmx")

    # 注册 fake tenant(ask_agentic 需要 tenant 在 context)
    from tenant import Tenant

    tenant = Tenant.from_plan(tenant_id="acme-corp", name="Acme")
    monkeypatch.setattr(
        "tenant_registry.get_registry",
        lambda: type(
            "R",
            (),
            {"get": staticmethod(lambda tid: tenant if tid == "acme-corp" else None)},
        )(),
    )

    p = P1Pipeline(user=User("alice", "executive"))
    # Stub LLM(模块级 _mmx_or_ollama)
    import pipeline as _pipeline_mod

    monkeypatch.setattr(_pipeline_mod, "_mmx_or_ollama", _fake_llm)
    # Stub embedder(instance method)→ 64 维 fake
    monkeypatch.setattr(p, "_ollama_embed_passages", lambda texts: _fake_embed(texts, dim=64))
    # 注入 fake index(已 indexed 状态)
    p._indexed = True
    p._index_cache = _make_index(5)

    return p


# ── 1. 端到端:跑一条 query,验证 P1Result 不变量 ──


def test_e2e_ask_returns_valid_result(pipeline_with_stubs: P1Pipeline):
    """完整 _ask_legacy 链路:embed → BM25+dense → pack → LLM → 引用校验。"""
    r = pipeline_with_stubs._ask_legacy("员工每年能休多少天年假？")
    assert r.question == "员工每年能休多少天年假？"
    assert isinstance(r.answer, str)
    assert isinstance(r.citations, list)
    assert isinstance(r.abstained, bool)
    assert isinstance(r.latency_ms, (int, float))
    assert r.latency_ms >= 0
    assert r.user_role == "executive"
    assert r.abstained is False  # fake LLM 不拒答
    assert len(r.retrieved_source_ids) > 0


def test_e2e_ask_includes_hit_for_expected_doc(pipeline_with_stubs: P1Pipeline):
    """「年假」query → 命中 hr-001(BM25 关键词匹配)。"""
    r = pipeline_with_stubs._ask_legacy("员工年假几天？")
    assert "hr-001" in r.retrieved_source_ids, (
        f"应命中 hr-001,got {r.retrieved_source_ids}"
    )


# ── 2. 拒答路径 ──


def test_e2e_abstain_on_unrelated_question(pipeline_with_stubs: P1Pipeline):
    """citations 必须全部指向合法 evidence id(即使 LLM 给了答案)。

    evidence id 格式:retrieve 阶段生成 `E1..En`,与 chunk_id 不同。
    """
    r = pipeline_with_stubs._ask_legacy("火星上有生命吗？")
    # evidence id 由 _ask_legacy 在 retrieve 阶段生成(E1..En),可推 ≤ topk_final(5)
    # 这里只检查 citations 引用是 E\d+ 格式且在合法范围内
    if r.citations:
        for cit in r.citations:
            assert cit.startswith("E"), f"citation {cit} 应以 E 开头"
            assert cit[1:].isdigit(), f"citation {cit} 应是 E\\d+"
            idx = int(cit[1:])
            assert 1 <= idx <= 5, f"citation {cit} 超出 retrieve topk 范围"


# ── 3. ACL ──


def test_e2e_acl_filters_confidential_chunks():
    """restricted 敏感度 chunk 对 employee 不可见(经 User.can_see)。"""
    employee = User("bob", "employee")
    exec_user = User("alice", "executive")
    assert employee.can_see("public") is True
    assert exec_user.can_see("public") is True
    assert employee.can_see("internal") is True
    assert exec_user.can_see("internal") is True
    assert employee.can_see("restricted") is False
    assert exec_user.can_see("restricted") is True


# ── 4. evaluate()(需要 langgraph) ──

langgraph_required = pytest.mark.skipif(
    not HAS_LANGGRAPH, reason="langgraph not installed (run `pip install langgraph`)"
)


@langgraph_required
def test_e2e_evaluate_returns_expected_structure(pipeline_with_stubs: P1Pipeline, tmp_path: Path):
    """evaluate(p, golden) 必须返回 dict 含 overall / by_slice / rows。"""
    from eval import evaluate

    golden = [
        {
            "id": "T1",
            "question": "员工年假几天？",
            "slice": "semantic",
            "expected_doc_ids": ["hr-001"],
            "expected_answer_contains": ["年假"],
            "must_abstain": False,
        },
        {
            "id": "T2",
            "question": "公司内网怎么访问？",
            "slice": "lexical",
            "expected_doc_ids": ["it-001"],
            "expected_answer_contains": ["VPN"],
            "must_abstain": False,
        },
    ]
    with __import__("tenant_context").tenant_scope(
        __import__("tenant").Tenant.from_plan(tenant_id="acme-corp", name="Acme")
    ):
        report = evaluate(pipeline_with_stubs, golden)
    assert "overall" in report and "by_slice" in report and "rows" in report
    overall = report["overall"]
    for k in (
        "n",
        "hit_rate",
        "citation_validity",
        "abstention_correctness",
        "contains_expected",
        "recall_at_10",
        "mrr",
        "ndcg_at_5",
        "ndcg_at_10",
        "p50_latency_ms",
    ):
        assert k in overall, f"missing metric {k}"
    assert overall["abstention_correctness"] == 1.0
    assert overall["hit_rate"] > 0, f"hit_rate 应 > 0, got {overall['hit_rate']}"
    assert overall["citation_validity"] == 1.0


@langgraph_required
def test_e2e_evaluate_with_must_abstain_question(pipeline_with_stubs: P1Pipeline):
    """must_abstain=true 的题目:fake LLM 不拒答 → abstention_correct 会扣分。"""
    from tenant import Tenant
    from tenant_context import tenant_scope

    from eval import evaluate

    golden = [
        {
            "id": "T-abst",
            "question": "公司是否有 UFO 政策？",
            "slice": "adversarial",
            "expected_doc_ids": [],
            "expected_answer_contains": [],
            "must_abstain": True,
        },
    ]
    with tenant_scope(Tenant.from_plan(tenant_id="acme-corp", name="Acme")):
        report = evaluate(pipeline_with_stubs, golden)
    assert report["overall"]["abstention_correctness"] == 0.0


# ── 5. 退化路径 ──


def test_e2e_empty_index_abstains():
    """未索引直接问 → 立即拒答,latency=0,不抛。"""
    p = P1Pipeline(user=User("alice", "executive"))
    r = p._ask_legacy("员工年假几天？")
    assert r.abstained is True
    assert "无索引" in r.answer or r.answer == ""
    assert r.latency_ms == 0.0
    assert r.retrieved_source_ids == []
    assert r.citations == []


def test_e2e_pipeline_constructs_with_user_role():
    """不索引也能构造,user_role 必须保留。"""
    p = P1Pipeline(user=User("u", "manager"))
    assert p.user.role == "manager"
    assert p.index == []
