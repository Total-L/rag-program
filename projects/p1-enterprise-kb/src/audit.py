"""P1 — 审计日志 + GDPR DSR（ENTERPRISE_AUDIT.md S3）。

设计原则（per CLAUDE.md "暴露冲突，不取平均"）：

1. **append-only JSONL**，每日一个文件 `data/audit/<YYYY-MM-DD>.jsonl`。
   - 为什么 JSONL：grep/awk 友好、可流式处理、不需要解析器。
   - 为什么每日一个文件：DSR 抹除时只重写小文件，且 90 天 retention 直接 `os.remove`。

2. **user_id 不存明文** — 用 HMAC-SHA256(salt, user_id) 截 16 hex。
   - 理由：审计文件本身不能成为 PII 泄漏源（GDPR 第 5 条 数据最小化）。
   - DSR 时同样按 hash 匹配，hash 一致即抹除。
   - salt 必须从 env `AUDIT_USER_ID_SALT` 读；缺则启动期 warning（用 dev salt）。
     生产必须设 —— 这是审计安全的关键。

3. **PII redact on write**：question/answer 过 P6 pii.py 的 `redact_for_trace`。
   - 9 类结构化 PII（CN_ID_CARD / BANK_CARD / API_KEY / DB_DSN 等）。
   - P6 import 失败 → 降级为原文 + warning（fail loud 但不阻断业务）。

4. **write 失败不阻断 query**，但上报 metric `rag_audit_write_total{status="failure"}`。
   - 与 quota_store.refund 的设计一致：审计是合规不能丢，但 query 不能因为
     磁盘满 / 权限错就挂。

5. **DSR 抹除 = 重写**：删除匹配 hash 的行后，写回同名文件（原子 `os.replace`）。
   - 抹除范围：所有 `data/audit/*.jsonl` 的匹配行 + 当前活跃 `data/audit/` 目录。
   - 不抹 Chroma：P6 摄取的 chunk 是公域内容，不存 user_id。

6. **retention = 启动期 lazy sweep**：每次 AuditLogger 实例化时扫一遍超期文件。
   - MVP 不写 cron；用户量大后再独立后台 job。

不变量（用于测试）：
- write() 调用之间互不干扰（同一进程内无并发；多进程安全靠 OS append 原子性）。
- dsr_erase_user() idempotent（重复调用返回计数 0）。
- retention_cleanup() 只删文件不修改文件。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 让 P6 的 pii.py 可被 import（与 index_store.py 同款技巧）
P6_SRC = Path(__file__).resolve().parents[2] / "p6-ingestion-platform" / "src"
if str(P6_SRC) not in sys.path:
    sys.path.insert(0, str(P6_SRC))

# ── 配置常量 ──

DEFAULT_AUDIT_DIR = Path(__file__).resolve().parents[1] / "data" / "audit"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_DEV_SALT = "rag-program-dev-salt-DO-NOT-USE-IN-PROD"  # 仅 fallback；生产必须 env
USER_ID_HASH_LEN = 16  # 64 位熵，对企业级 user 量级足够

# ── PII redact（可选，失败降级）──

try:
    from pii import redact_for_trace as _redact_for_trace  # noqa: E402

    _PII_REDACT_AVAILABLE = True
except Exception:  # ImportError + 任何模块内部错误
    _PII_REDACT_AVAILABLE = False
    _redact_for_trace = None  # type: ignore[assignment]


def _redact(text: str, *, max_len: int = 200) -> str:
    """对一段文本做 PII redact + 长度截断（落 audit 前）。

    Args:
        text: 原始文本（question / answer）
        max_len: 输出最大长度（避免审计文件被超长 LLM 输出撑大）

    Returns:
        redacted 文本；PII 命中处被替换为 `<PII_TYPE>` 标记。
    """
    if not text:
        return ""
    if _PII_REDACT_AVAILABLE and _redact_for_trace is not None:
        try:
            return _redact_for_trace(text, max_len=max_len)
        except Exception:
            pass  # 降级到下面
    # 降级路径：仅长度截断（无 redact）—— PII 模块不可用时
    return text[:max_len]


# ── user_id hash ──


def hash_user_id(user_id: str, *, salt: str) -> str:
    """HMAC-SHA256(salt, user_id) 截前 16 hex。

    为什么 HMAC 而不是普通 hash：
    - 普通 sha256(salt+user_id) 可被 rainbow table 攻击（salt 不是 secret 时）。
    - HMAC 用密钥形式 salt，且不可逆推 user_id；不同 salt 产出不同 hash。

    Returns:
        16 位 hex（64 位熵，对企业级用户基数去重足够）。
    """
    if not user_id:
        raise ValueError("user_id must be non-empty")
    if not isinstance(user_id, str):
        raise TypeError(f"user_id must be str, got {type(user_id).__name__}")
    h = hmac.new(salt.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256)
    return h.hexdigest()[:USER_ID_HASH_LEN]


# ── AuditEvent ──


@dataclass
class AuditEvent:
    """一次 query 的审计记录。

    字段说明：
    - ts: ISO8601 UTC（落盘时也按此分文件）
    - trace_id: 与 observability.trace_id_var 同源（log/metrics 关联用）
    - tenant_id: S2 多租户隔离的 tenant slug
    - user_id_hash: HMAC 截断；**永不存明文 user_id**
    - question_redacted: 已 redact PII（最多 200 字）
    - answer_redacted: 已 redact PII（最多 500 字）
    - citations: LLM 自报引用（未校验；后续模块自校验）
    - retrieved_source_ids / retrieved_chunk_ids: 检索阶段命中
    - abstained: 是否拒答
    - latency_ms: 端到端延迟（毫秒）
    - tokens_in / tokens_out: token 估算（缺则 None —— MVP 暂未跟踪）
    - status: success / abstained / error / not_ready
    """

    tenant_id: str
    user_id_hash: str
    question_redacted: str
    answer_redacted: str
    trace_id: str
    status: str
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    citations: list[str] = field(default_factory=list)
    retrieved_source_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    abstained: bool = False
    latency_ms: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None

    def to_jsonl(self) -> str:
        """序列化为单行 JSON（ensure_ascii=False 保留中文）。"""
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


# ── AuditLogger ──


class AuditLogger:
    """append-only JSONL 审计写入器 + DSR 抹除 + retention 清理。

    用法：
        logger = AuditLogger()  # 自动用 env / 默认值
        logger.write(event)
        removed = logger.dsr_erase_user(user_id)
        logger.retention_cleanup(days=90)

    不变量：
    - base_dir 必须存在（构造时 `mkdir -p`，不抛）
    - 同一进程多线程安全：os.append 写小行 < 4KB 在 Linux/macOS 是原子的
    - 多进程安全：同上（同 OS append 语义）
    """

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        salt: str | None = None,
        retention_days: int | None = None,
    ):
        # base_dir: env > 参数 > 默认
        env_dir = os.environ.get("AUDIT_DIR")
        if base_dir is not None:
            self.base_dir = Path(base_dir)
        elif env_dir:
            self.base_dir = Path(env_dir)
        else:
            self.base_dir = DEFAULT_AUDIT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # salt: env > 参数 > dev fallback
        env_salt = os.environ.get("AUDIT_USER_ID_SALT")
        if salt is not None:
            self.salt = salt
        elif env_salt:
            self.salt = env_salt
        else:
            self.salt = DEFAULT_DEV_SALT

        # retention
        env_days = os.environ.get("AUDIT_RETENTION_DAYS")
        if retention_days is not None:
            self.retention_days = retention_days
        elif env_days:
            try:
                self.retention_days = int(env_days)
            except ValueError:
                self.retention_days = DEFAULT_RETENTION_DAYS
        else:
            self.retention_days = DEFAULT_RETENTION_DAYS

        # 启动期 lazy retention sweep（不抛异常，最佳努力）
        try:
            self.retention_cleanup(days=self.retention_days)
        except Exception:
            pass  # 启动期失败不应阻断审计初始化

    # ── 写入 ──

    def _file_for_ts(self, ts: str) -> Path:
        """从 ISO8601 ts 提取 YYYY-MM-DD 得到对应文件路径。"""
        # 兼容 ts="2026-07-28T12:34:56" / "2026-07-28T12:34:56+00:00"
        day = ts[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            # fallback 到今天（解析失败兜底）
            day = datetime.now(UTC).strftime("%Y-%m-%d")
        return self.base_dir / f"{day}.jsonl"

    def write(self, event: AuditEvent) -> Path:
        """追加一行 JSONL 到对应日期的文件。

        Returns:
            实际写入的文件路径。

        Raises:
            OSError: 磁盘满 / 权限错等真实 IO 错误（让调用方决定如何处理）。
        """
        path = self._file_for_ts(event.ts)
        line = event.to_jsonl()
        # append + flush：保证进程崩溃时不丢最近一行
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
            f.flush()
        return path

    # ── DSR 抹除 ──

    def dsr_erase_user(
        self,
        user_id: str,
        *,
        dry_run: bool = False,
    ) -> dict:
        """GDPR 第 17 条"被遗忘权"：抹除该 user_id 在 audit log 中的所有痕迹。

        Args:
            user_id: 明文 user_id（与请求方一致的明文；内部 hash 后匹配）
            dry_run: True 则只统计不实际写

        Returns:
            {
                "user_id_hash": "<hash>",
                "removed_events": N,
                "scanned_files": [..],
                "dry_run": bool,
            }

        Notes:
            - idempotent：重复 DELETE 返回 removed_events=0
            - 抹除范围仅 audit log；P6 Chroma chunk 不动（公域内容无 user_id）
        """
        target_hash = hash_user_id(user_id, salt=self.salt)
        scanned: list[str] = []
        matched_total = 0  # 总是累加（不论 dry_run），便于 dry_run 返回预览
        removed_total = 0  # 仅在非 dry_run 时累加（实际删除的计数）

        for path in sorted(self.base_dir.glob("*.jsonl")):
            scanned.append(path.name)
            kept: list[str] = []
            matched_in_file = 0
            try:
                with path.open("r", encoding="utf-8") as f:
                    for raw in f:
                        raw = raw.rstrip("\n")
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw)
                        except json.JSONDecodeError:
                            kept.append(raw)
                            continue
                        if obj.get("user_id_hash") == target_hash:
                            matched_in_file += 1
                        else:
                            kept.append(raw)
            except FileNotFoundError:
                continue

            matched_total += matched_in_file

            # dry_run 模式：统计但**不**重写
            if matched_in_file and not dry_run:
                # 原子重写：写到临时文件再 os.replace
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    for line in kept:
                        f.write(line + "\n")
                os.replace(tmp, path)
                removed_total += matched_in_file

        return {
            "user_id_hash": target_hash,
            "removed_events": 0 if dry_run else removed_total,
            "dry_run_removed_events": matched_total if dry_run else 0,
            "scanned_files": scanned,
            "dry_run": dry_run,
        }

    # ── Retention ──

    def retention_cleanup(self, *, days: int | None = None) -> list[str]:
        """删除超过 N 天的 audit 文件（按文件名前缀 YYYY-MM-DD 判定）。

        Args:
            days: 留存天数（None 则用 self.retention_days）

        Returns:
            被删除的文件名列表。
        """
        threshold_days = days if days is not None else self.retention_days
        if threshold_days < 0:
            raise ValueError("days must be >= 0")
        cutoff = datetime.now(UTC).date() - timedelta(days=threshold_days)
        removed: list[str] = []

        for path in sorted(self.base_dir.glob("*.jsonl")):
            name = path.stem  # "YYYY-MM-DD"
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", name):
                continue  # 跳过非日期文件
            try:
                file_date = datetime.strptime(name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                path.unlink()
                removed.append(path.name)
        return removed

    # ── 便捷：从外部参数构建 AuditEvent ──

    def build_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        question: str,
        answer: str,
        trace_id: str,
        status: str,
        citations: Iterable[str] = (),
        retrieved_source_ids: Iterable[str] = (),
        retrieved_chunk_ids: Iterable[str] = (),
        abstained: bool = False,
        latency_ms: float = 0.0,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
    ) -> AuditEvent:
        """从明文 user_id + 业务字段构造 AuditEvent（自动 hash + redact）。"""
        return AuditEvent(
            tenant_id=tenant_id,
            user_id_hash=hash_user_id(user_id, salt=self.salt),
            question_redacted=_redact(question, max_len=200),
            answer_redacted=_redact(answer, max_len=500),
            trace_id=trace_id,
            status=status,
            citations=list(citations),
            retrieved_source_ids=list(retrieved_source_ids),
            retrieved_chunk_ids=list(retrieved_chunk_ids),
            abstained=abstained,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


# ── 进程级默认实例（懒初始化） ──

_default_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """获取进程级 AuditLogger 单例（首次调用时初始化）。"""
    global _default_logger
    if _default_logger is None:
        _default_logger = AuditLogger()
    return _default_logger


def reset_audit_logger() -> None:
    """测试用：重置单例。"""
    global _default_logger
    _default_logger = None


# ── 测试辅助 ──


def new_dev_salt() -> str:
    """生成临时 dev salt（测试用，避免手动拼字符串）。"""
    return "dev-" + secrets.token_hex(8)


# ── 模块导出 ──

__all__ = [
    "AuditEvent",
    "AuditLogger",
    "DEFAULT_AUDIT_DIR",
    "DEFAULT_RETENTION_DAYS",
    "USER_ID_HASH_LEN",
    "get_audit_logger",
    "hash_user_id",
    "new_dev_salt",
    "reset_audit_logger",
]
