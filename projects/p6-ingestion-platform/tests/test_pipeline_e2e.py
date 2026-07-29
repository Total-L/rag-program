"""P6 端到端流水线测试 —— "整个流程"的回归保障。

用 `HashEmbedder`（确定性、无需 Ollama），所以 CI 里也能跑。
这里验证的是**流程语义**，不是检索质量：
1. 幂等：重跑 0 写入
2. 编辑文档 → 旧 chunk 被清除（不留僵尸向量）
3. 源侧删除 → 向量库同步删除
4. PII 不进向量库
5. 坏文档进 DLQ，不影响其它文档
6. 质量过滤 / 近重复真的拦住了东西
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from run_ingest import IngestPipeline  # noqa: E402
from src.connectors import FileSystemConnector  # noqa: E402
from src.embeddings import HashEmbedder  # noqa: E402
from src.manifest import ManifestStore  # noqa: E402
from src.store import ChromaIndexStore  # noqa: E402

DOC_A = """# 报销政策

## 差旅限额
市内交通每人每天上限二百元，需提供正规票据。住宿标准一线城市每晚八百元。
单笔支出超过五千元的，必须事先取得部门总监书面批准。
"""

DOC_B = """# 入职指南

## 第一周
领取设备并完成安全培训。阅读公司行为准则与信息安全规范。
与直属主管确认三个月内的工作目标与考核方式。
"""


@pytest.fixture()
def env(tmp_path: Path):
    """搭一套独立的摄取环境。"""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text(DOC_A, encoding="utf-8")
    (corpus / "b.md").write_text(DOC_B, encoding="utf-8")

    store = ChromaIndexStore(tmp_path / "index", collection_name="p6_e2e_test")
    manifest = ManifestStore(tmp_path / "manifest.db")
    pipe = IngestPipeline(
        store=store,
        manifest=manifest,
        embedder=HashEmbedder(dim=64),
        dlq_dir=tmp_path / "dlq",
        neardup_threshold=0.95,  # 两篇内容差异大，不会互相误判
    )
    yield {"corpus": corpus, "store": store, "manifest": manifest, "pipe": pipe, "tmp": tmp_path}
    manifest.close()


def _run(env, run_id: str, full_scan: bool = True):
    conn = FileSystemConnector(env["corpus"], patterns=("*.md",))
    return env["pipe"].run([conn], run_id=run_id, full_scan=full_scan)


# ── ① 首轮 + 幂等 ─────────────────────────────────────
def test_first_run_indexes_both_docs(env):
    st = _run(env, "r1")
    assert st.listed == 2
    assert st.docs_indexed == 2, f"两篇都该入库, {st.to_dict()}"
    assert st.chunks_written > 0
    assert st.failures == []
    assert env["store"].count() == st.chunks_written


def test_rerun_writes_nothing(env):
    """★ 核心：同一批数据重跑，0 重嵌、0 写入、行数不变。"""
    st1 = _run(env, "r1")
    count_after_first = env["store"].count()

    st2 = _run(env, "r2")
    assert st2.new == 0 and st2.changed == 0, f"重跑不该有 new/changed: {st2.to_dict()}"
    assert st2.unchanged == 2
    assert st2.docs_indexed == 0
    assert st2.chunks_written == 0
    assert env["store"].count() == count_after_first, "行数必须不变（幂等）"
    assert st1.chunks_written > 0  # 确认第一轮真的写了东西


# ── ② 编辑文档 → 清僵尸 ───────────────────────────────
def test_edit_document_removes_stale_chunks(env):
    """★ 核心：文档改了，旧 chunk 必须消失，不能留僵尸向量。"""
    _run(env, "r1")
    ids_before = env["store"].existing_chunk_ids("fs:a.md")
    total_before = env["store"].count()
    assert ids_before

    # 改写 a.md（内容变了 → chunk_id 变了）
    (env["corpus"] / "a.md").write_text(
        DOC_A.replace("二百元", "三百元").replace("八百元", "九百元"),
        encoding="utf-8",
    )
    st = _run(env, "r2")
    assert st.changed == 1, f"应检测到 1 篇变更: {st.to_dict()}"
    assert st.docs_indexed == 1

    ids_after = env["store"].existing_chunk_ids("fs:a.md")
    assert ids_after != ids_before, "chunk_id 应随内容变化"
    # 关键断言：旧 chunk 不能残留
    assert not (ids_before & ids_after), f"僵尸 chunk 残留: {ids_before & ids_after}"
    assert st.stale_removed > 0, "应报告清除了失效 chunk"
    # b.md 不受影响
    assert env["store"].existing_chunk_ids("fs:b.md"), "另一篇文档不该被牵连"
    # 总行数不该无限膨胀
    assert env["store"].count() <= total_before + len(ids_after), "行数不该累积膨胀"


def test_edited_content_is_searchable_and_old_gone(env):
    _run(env, "r1")
    (env["corpus"] / "a.md").write_text(DOC_A.replace("二百元", "三百元"), encoding="utf-8")
    _run(env, "r2")
    texts = " ".join(
        h["text"]
        for h in env["store"].query(HashEmbedder(dim=64).embed_query("市内交通 限额"), k=20)
    )
    assert "三百元" in texts, "新内容应可检索"
    assert "二百元" not in texts, "旧内容必须彻底消失"


# ── ③ 源侧删除 → 向量库同步删除 ────────────────────────
def test_source_deletion_removes_vectors(env):
    _run(env, "r1")
    assert env["store"].existing_chunk_ids("fs:b.md")

    (env["corpus"] / "b.md").unlink()
    st = _run(env, "r2", full_scan=True)
    assert st.deleted == 1, f"应识别删除: {st.to_dict()}"
    assert env["store"].existing_chunk_ids("fs:b.md") == set(), "向量必须被删除"
    assert env["store"].existing_chunk_ids("fs:a.md"), "另一篇不该被删"


def test_incremental_mode_does_not_delete(env):
    """增量模式不做删除判定 —— 防止限流/权限问题导致误删。"""
    _run(env, "r1")
    (env["corpus"] / "b.md").unlink()
    st = _run(env, "r2", full_scan=False)
    assert st.deleted == 0, "增量模式不该删除"


# ── ④ PII 不进向量库 ─────────────────────────────────
def test_pii_never_reaches_vector_store(env):
    """★ 泄漏率 = 0：入库文本里不能出现明文 PII。"""
    (env["corpus"] / "leak.md").write_text(
        "# 员工档案\n\n"
        "轮值联系人手机 13812345678，身份证 310101199001011234。\n"
        "生产库 postgresql://svc:Pr0dP%40ss@10.20.30.40:5432/kb\n"
        "AWS 凭证 AKIAIOSFODNN7EXAMPLE\n"
        "以上信息仅限内部使用，请勿外传给任何第三方合作机构。\n",
        encoding="utf-8",
    )
    st = _run(env, "r1")
    assert st.pii_redacted_docs >= 1, "应报告脱敏了文档"

    all_text = " ".join(
        h["text"]
        for h in env["store"].query(HashEmbedder(dim=64).embed_query("员工 档案 联系人"), k=50)
    )
    for leaked in ["13812345678", "310101199001011234", "Pr0dP%40ss", "AKIAIOSFODNN7EXAMPLE"]:
        assert leaked not in all_text, f"PII 泄漏进向量库: {leaked}"


def test_sensitivity_auto_upgraded_in_metadata(env):
    """含身份证的文档，落库 metadata 的 sensitivity 应被自动升级。"""
    (env["corpus"] / "idcard.md").write_text(
        "---\nsensitivity: public\n---\n"
        "# 名单\n\n本次轮值同学身份证号码为 310101199001011234，请核对无误后签字确认。\n",
        encoding="utf-8",
    )
    _run(env, "r1")
    hits = [
        h
        for h in env["store"].query(HashEmbedder(dim=64).embed_query("轮值 身份证 名单"), k=50)
        if h["metadata"].get("source_id") == "fs:idcard.md"
    ]
    assert hits, "应能取到该文档的 chunk"
    assert hits[0]["metadata"]["sensitivity"] == "restricted", (
        f"声明 public 但含身份证 → 应升级为 restricted, got {hits[0]['metadata']}"
    )


# ── ⑤ 坏文档进 DLQ，不拖垮整轮 ─────────────────────────
def test_bad_document_goes_to_dlq_without_killing_run(env, monkeypatch):
    """★ 逐文档隔离：一篇坏文档不能炸掉整轮摄取。"""
    real_fetch = FileSystemConnector.fetch_body

    def flaky(self, rec):
        if rec.source_id.endswith("b.md"):
            raise OSError("模拟磁盘读取失败")
        return real_fetch(self, rec)

    monkeypatch.setattr(FileSystemConnector, "fetch_body", flaky)
    st = _run(env, "r1")

    assert len(st.failures) == 1, f"应有 1 篇失败: {st.to_dict()}"
    assert st.failures[0].source_id == "fs:b.md"
    assert st.docs_indexed == 1, "另一篇必须照常入库"

    dlq = env["tmp"] / "dlq" / "failed.jsonl"
    assert dlq.exists(), "失败必须落 DLQ"
    rec = json.loads(dlq.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["source_id"] == "fs:b.md"
    assert "磁盘读取失败" in rec["error"]


def test_failed_document_is_retried_next_run(env, monkeypatch):
    """失败的文档不写 manifest → 下轮自动重试，不需要额外重试队列。"""
    real_fetch = FileSystemConnector.fetch_body
    fail = {"on": True}

    def flaky(self, rec):
        if fail["on"] and rec.source_id.endswith("b.md"):
            raise OSError("暂时失败")
        return real_fetch(self, rec)

    monkeypatch.setattr(FileSystemConnector, "fetch_body", flaky)
    st1 = _run(env, "r1")
    assert len(st1.failures) == 1

    fail["on"] = False  # 故障恢复
    st2 = _run(env, "r2")
    assert st2.docs_indexed == 1, f"上轮失败的文档应被重试: {st2.to_dict()}"
    assert env["store"].existing_chunk_ids("fs:b.md"), "重试后应入库"


# ── ⑥ 质量过滤 / 近重复真的生效 ────────────────────────
def test_low_quality_document_is_skipped(env):
    (env["corpus"] / "toc.md").write_text(
        "# 目录\n\n1. 总则.......... 3\n2. 范围.......... 7\n3. 附则.......... 9\n",
        encoding="utf-8",
    )
    st = _run(env, "r1")
    assert st.skipped_quality >= 1, f"目录页应被质量过滤挡住: {st.to_dict()}"
    assert env["store"].existing_chunk_ids("fs:toc.md") == set()


def test_near_duplicate_document_is_skipped(env, tmp_path):
    """整篇只改一处措辞的重复文档应被拦下（省嵌入成本 + 不污染召回）。

    注意用**文档级长度**的文本 + 文档级阈值 0.85：
    短文本上一处小改动的相似度只有 ~0.7（见 test_clean 的校准测试），
    用 0.95 抓不到 —— 这不是 bug，是 MinHash 的固有特性。
    """
    long_doc = (
        "# 采购管理制度\n\n"
        "第一条 本制度规范公司所有物资与服务的采购行为。\n"
        "第二条 单笔金额一万元以下由部门负责人审批。\n"
        "第三条 单笔金额一万元以上十万元以下需报分管副总审批。\n"
        "第四条 单笔金额超过十万元的须提交采购委员会评审。\n"
        "第五条 供应商准入需完成资质审查与信用评估两道流程。\n"
        "第六条 采购合同必须由法务部门完成合规审查后方可签署。\n"
        "第七条 验收环节应由使用部门与仓储部门共同签字确认。\n"
        "第八条 违反本制度的相关责任人将按公司纪律条例处理。\n"
    )
    (env["corpus"] / "purchase.md").write_text(long_doc, encoding="utf-8")
    (env["corpus"] / "purchase_copy.md").write_text(
        long_doc.replace("方可签署", "才可签署"), encoding="utf-8"
    )
    # 用文档级阈值重建一条流水线
    pipe = IngestPipeline(
        store=env["store"],
        manifest=env["manifest"],
        embedder=HashEmbedder(dim=64),
        dlq_dir=env["tmp"] / "dlq2",
        neardup_threshold=0.85,
    )
    conn = FileSystemConnector(env["corpus"], patterns=("*.md",))
    st = pipe.run([conn], run_id="r1", full_scan=True)
    assert st.skipped_neardup >= 1, f"近重复应被拦下: {st.to_dict()}"


def test_stats_json_report_is_written(env, tmp_path):
    st = _run(env, "r1")
    d = st.to_dict()
    assert d["run_id"] == "r1"
    assert isinstance(d["failures"], int), "报告里 failures 应是计数便于比对阈值"
    assert d["chunks_written"] > 0
