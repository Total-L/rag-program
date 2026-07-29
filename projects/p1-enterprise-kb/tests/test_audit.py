"""P1 — 审计日志 + GDPR DSR 测试（ENTERPRISE_AUDIT.md S3）。

覆盖：
1. AuditEvent JSONL 序列化
2. AuditLogger write 单行 / append / 跨日切文件 / PII redact / 写失败不抛
3. hash_user_id 一致 / salt 不同值不同 / 空 user_id 拒绝
4. dsr_erase_user 抹除匹配行 / dry_run / 幂等
5. retention_cleanup 删除超期文件 / 保留近期

跑：
    pytest projects/p1-enterprise-kb/tests/test_audit.py -v
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import audit
import pytest
from audit import (
    DEFAULT_RETENTION_DAYS,
    AuditEvent,
    AuditLogger,
    hash_user_id,
    new_dev_salt,
)

# ── fixtures ──


@pytest.fixture
def tmp_audit_dir(tmp_path: Path) -> Path:
    """每个测试一个独立 audit 目录；用完清理。"""
    d = tmp_path / "audit"
    d.mkdir()
    return d


@pytest.fixture
def salt() -> str:
    """dev salt，每个测试固定一份（HMAC 一致性测试需要）。"""
    return "test-salt-do-not-use-in-prod"


@pytest.fixture
def logger(tmp_audit_dir: Path, salt: str) -> AuditLogger:
    """标准 AuditLogger 实例（自定义 base_dir + salt）。"""
    return AuditLogger(base_dir=tmp_audit_dir, salt=salt, retention_days=30)


# ── 1. AuditEvent 序列化 ──


def test_event_round_trip():
    """AuditEvent → JSONL → dict 字段一致。"""
    e = AuditEvent(
        tenant_id="acme-corp",
        user_id_hash="a" * 16,
        question_redacted="员工年假？",
        answer_redacted="根据手册第 5 章...",
        trace_id="t-001",
        status="success",
        citations=["E1", "E2"],
        retrieved_source_ids=["handbook-v3"],
        retrieved_chunk_ids=["abc123"],
        abstained=False,
        latency_ms=123.4,
        tokens_in=10,
        tokens_out=20,
    )
    line = e.to_jsonl()
    obj = json.loads(line)
    assert obj["tenant_id"] == "acme-corp"
    assert obj["user_id_hash"] == "a" * 16
    assert obj["citations"] == ["E1", "E2"]
    assert obj["latency_ms"] == pytest.approx(123.4)
    assert obj["tokens_in"] == 10
    # 不应有 user_id 明文
    assert "user_id" not in obj
    assert "alice@acme.com" not in line


def test_event_default_ts_is_iso8601():
    """未传 ts 则默认当下 UTC ISO8601（带时区后缀 +00:00）。"""
    e = AuditEvent(
        tenant_id="t", user_id_hash="b" * 16, question_redacted="q",
        answer_redacted="a", trace_id="tid", status="success",
    )
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?", e.ts
    )


# ── 2. AuditLogger write ──


def test_write_creates_dated_file(logger: AuditLogger, tmp_audit_dir: Path):
    """第一次 write 应创建 <YYYY-MM-DD>.jsonl。"""
    e = AuditEvent(
        tenant_id="acme-corp", user_id_hash="c" * 16, question_redacted="q",
        answer_redacted="a", trace_id="tid", status="success",
    )
    path = logger.write(e)
    assert path.exists()
    assert path.name == f"{e.ts[:10]}.jsonl"
    content = path.read_text(encoding="utf-8").strip()
    obj = json.loads(content)
    assert obj["tenant_id"] == "acme-corp"


def test_write_appends_to_existing_file(logger: AuditLogger, tmp_audit_dir: Path):
    """同一日多次 write 追加到同一文件。"""
    for i in range(3):
        e = AuditEvent(
            tenant_id="t", user_id_hash=str(i) * 16, question_redacted=f"q{i}",
            answer_redacted="a", trace_id=f"t{i}", status="success",
        )
        logger.write(e)
    files = list(tmp_audit_dir.glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["trace_id"] == "t0"
    assert json.loads(lines[2])["trace_id"] == "t2"


def test_write_cross_day_rotates_file(logger: AuditLogger, tmp_audit_dir: Path):
    """跨日事件落到两个文件。"""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")

    e1 = AuditEvent(
        tenant_id="t", user_id_hash="d" * 16, question_redacted="q1",
        answer_redacted="a", trace_id="t1", status="success",
        ts=f"{today}T10:00:00+00:00",
    )
    e2 = AuditEvent(
        tenant_id="t", user_id_hash="e" * 16, question_redacted="q2",
        answer_redacted="a", trace_id="t2", status="success",
        ts=f"{yesterday}T10:00:00+00:00",
    )
    logger.write(e1)
    logger.write(e2)
    assert (tmp_audit_dir / f"{today}.jsonl").exists()
    assert (tmp_audit_dir / f"{yesterday}.jsonl").exists()


def test_write_redacts_pii_in_question_and_answer(tmp_audit_dir: Path, salt: str):
    """build_event 对含手机号 / 身份证的 q/a 做 redact。"""
    logger = AuditLogger(base_dir=tmp_audit_dir, salt=salt, retention_days=30)
    evt = logger.build_event(
        tenant_id="acme-corp",
        user_id="alice",
        question="我的手机号 13800138000 怎么报？",
        answer="请发邮件到 alice@acme.com 或致电 13800138000",
        trace_id="t",
        status="success",
    )
    # 手机号 / 邮箱应被 redact 为标记
    assert "13800138000" not in evt.question_redacted
    assert "alice@acme.com" not in evt.answer_redacted
    assert "13800138000" not in evt.answer_redacted


def test_write_failure_does_not_raise(tmp_audit_dir: Path, salt: str, monkeypatch):
    """AuditLogger write 在底层抛 OSError 时上抛（由调用方决定如何处理）。

    pipeline.ask_agentic 是吞掉异常的层；这里验证底层契约是 raise。
    """
    logger = AuditLogger(base_dir=tmp_audit_dir, salt=salt, retention_days=30)
    # 替换内部 file open → 抛 OSError
    def _boom(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(Path, "open", _boom)
    e = AuditEvent(
        tenant_id="t", user_id_hash="f" * 16, question_redacted="q",
        answer_redacted="a", trace_id="t", status="success",
    )
    with pytest.raises(OSError, match="simulated disk full"):
        logger.write(e)


# ── 3. hash_user_id ──


def test_hash_user_id_deterministic(salt: str):
    """同一 (user_id, salt) → 同一 hash。"""
    h1 = hash_user_id("alice", salt=salt)
    h2 = hash_user_id("alice", salt=salt)
    assert h1 == h2
    assert len(h1) == 16  # 16 hex chars = 64 bits


def test_hash_user_id_different_salt_changes_value():
    """换 salt → 同 user_id 也产出不同 hash（HMAC 不可推 salt）。"""
    h1 = hash_user_id("alice", salt="salt-A")
    h2 = hash_user_id("alice", salt="salt-B")
    assert h1 != h2


def test_hash_user_id_rejects_empty(salt: str):
    """空 user_id 拒绝。"""
    with pytest.raises(ValueError, match="non-empty"):
        hash_user_id("", salt=salt)


def test_hash_user_id_rejects_non_string(salt: str):
    """非 str user_id 拒绝。"""
    with pytest.raises(TypeError, match="must be str"):
        hash_user_id(123, salt=salt)  # type: ignore[arg-type]


# ── 4. DSR 抹除 ──


def _seed_two_users(logger: AuditLogger) -> tuple[str, str]:
    """写入 2 个用户各 2 条事件；返回 (alice_hash, bob_hash)。"""
    alice = hash_user_id("alice", salt=logger.salt)
    bob = hash_user_id("bob", salt=logger.salt)
    for h, trace in [(alice, "a1"), (alice, "a2"), (bob, "b1"), (bob, "b2")]:
        e = AuditEvent(
            tenant_id="t", user_id_hash=h, question_redacted="q",
            answer_redacted="a", trace_id=trace, status="success",
        )
        logger.write(e)
    return alice, bob


def test_dsr_erase_user_removes_matching_lines(logger: AuditLogger, tmp_audit_dir: Path):
    """DSR 抹除匹配 user_id_hash 的所有行。"""
    alice_hash, bob_hash = _seed_two_users(logger)
    result = logger.dsr_erase_user("alice")
    assert result["user_id_hash"] == alice_hash
    assert result["removed_events"] == 2
    assert result["dry_run"] is False

    # 残留文件应只含 bob 的 2 行
    remaining = []
    for path in tmp_audit_dir.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                remaining.append(json.loads(line))
    assert len(remaining) == 2
    assert all(r["user_id_hash"] == bob_hash for r in remaining)


def test_dsr_erase_user_dry_run_returns_count_without_writing(
    logger: AuditLogger, tmp_audit_dir: Path
):
    """dry_run=True 不实际删。"""
    _seed_two_users(logger)
    result = logger.dsr_erase_user("alice", dry_run=True)
    assert result["dry_run_removed_events"] == 2
    assert result["removed_events"] == 0
    # 文件未变
    lines = []
    for path in tmp_audit_dir.glob("*.jsonl"):
        lines.extend(path.read_text(encoding="utf-8").splitlines())
    assert len([line for line in lines if line.strip()]) == 4


def test_dsr_erase_user_idempotent(logger: AuditLogger):
    """重复 DELETE 第二次返回 0。"""
    _seed_two_users(logger)
    r1 = logger.dsr_erase_user("alice")
    r2 = logger.dsr_erase_user("alice")
    assert r1["removed_events"] == 2
    assert r2["removed_events"] == 0


def test_dsr_erase_user_unknown_user(logger: AuditLogger):
    """未注册 user_id → removed_events=0，不报错。"""
    _seed_two_users(logger)
    result = logger.dsr_erase_user("ghost")
    assert result["removed_events"] == 0
    assert result["user_id_hash"] == hash_user_id("ghost", salt=logger.salt)


# ── 5. retention ──


def test_retention_cleanup_removes_old_files(tmp_audit_dir: Path, salt: str):
    """超过留存天数的文件被删除。

    注意：构造 AuditLogger(retention_days=180) 让 lazy sweep **不**删 100 天前的旧文件；
    然后手动 retention_cleanup(days=90) 删除它——验证 cleanup 自身的逻辑。
    """
    logger = AuditLogger(base_dir=tmp_audit_dir, salt=salt, retention_days=180)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    old = (datetime.now(UTC) - timedelta(days=100)).strftime("%Y-%m-%d")
    recent = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

    for day in (today, recent, old):
        (tmp_audit_dir / f"{day}.jsonl").write_text("{}\n", encoding="utf-8")
    # 也写一个非日期命名文件（不应被删）
    (tmp_audit_dir / "notes.txt").write_text("keep me", encoding="utf-8")

    # lazy sweep (180d) 不会清 100 天前的文件
    assert (tmp_audit_dir / f"{old}.jsonl").exists()

    removed = logger.retention_cleanup(days=90)
    assert f"{old}.jsonl" in removed
    assert (tmp_audit_dir / f"{old}.jsonl").exists() is False
    assert (tmp_audit_dir / f"{today}.jsonl").exists() is True
    assert (tmp_audit_dir / f"{recent}.jsonl").exists() is True
    assert (tmp_audit_dir / "notes.txt").exists() is True


def test_retention_cleanup_keeps_all_when_zero_days(tmp_audit_dir: Path, salt: str):
    """retention_days=0 时只删今天之前的（实际是昨天及之前）—— 至少留当天。"""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    (tmp_audit_dir / f"{today}.jsonl").write_text("{}\n", encoding="utf-8")
    logger = AuditLogger(base_dir=tmp_audit_dir, salt=salt, retention_days=0)
    removed = logger.retention_cleanup(days=0)
    # cutoff = today - 0d = today；file_date < cutoff → 不删今天的
    assert removed == []


# ── 6. 模块 API 完整性 ──


def test_default_retention_constant_is_90():
    """默认留存 90 天（GDPR 行业惯例）。"""
    assert DEFAULT_RETENTION_DAYS == 90


def test_new_dev_salt_returns_unique():
    """new_dev_salt 每次生成不同 salt。"""
    s1 = new_dev_salt()
    s2 = new_dev_salt()
    assert s1 != s2
    assert s1.startswith("dev-")


def test_module_exports_expected_symbols():
    """__all__ 与 import 路径一致。"""
    expected = {
        "AuditEvent",
        "AuditLogger",
        "DEFAULT_AUDIT_DIR",
        "DEFAULT_RETENTION_DAYS",
        "USER_ID_HASH_LEN",
        "get_audit_logger",
        "hash_user_id",
        "new_dev_salt",
        "reset_audit_logger",
    }
    assert expected.issubset(set(audit.__all__))


# ── 7. process-level singleton（确保 server 端集成工作）──


def test_get_audit_logger_returns_singleton(tmp_audit_dir: Path, monkeypatch):
    """get_audit_logger 返回同一实例。"""
    monkeypatch.setenv("AUDIT_DIR", str(tmp_audit_dir))
    audit.reset_audit_logger()
    a1 = audit.get_audit_logger()
    a2 = audit.get_audit_logger()
    assert a1 is a2
    audit.reset_audit_logger()
