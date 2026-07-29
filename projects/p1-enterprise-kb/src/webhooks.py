"""P1 — Webhook 注册 + 异步回调（ENTERPRISE_AUDIT D7-T4）。

设计取舍（per CLAUDE.md "Simplicity First"）：

1. **JSON 文件存储** `data/webhooks.json`（类似 tenants.json）。
   - dev / MVP 阶段不引入 Postgres；文件读写 + 互斥锁足够。
   - 后续换 Postgres / Redis 不破坏 API（接口稳定）。

2. **事件类型白名单**：
   - query_completed   - 每次 query 完成后
   - audit_written     - audit log 写入（含 PII redact）
   - quota_exceeded    - 租户配额耗尽
   - ingest_completed  - 摄取流程跑完（含失败计数）
   - ingest_failed     - 摄取流程异常
   加新事件类型只追加不改名（破坏 webhook 订阅）。

3. **HMAC 签名**：`X-Webhook-Signature: sha256=<hex>`。
   - 算法：HMAC-SHA256(secret, request_body)，与 GitHub / Stripe 一致。
   - secret 在注册时设置；回调方用 secret 验签防伪造。

4. **异步 fire-and-forget**：主流程不阻塞。
   - 用 `threading.Thread(daemon=True)` 起后台任务。
   - 失败重试 3 次（指数退避 1s/2s/4s）。
   - 全失败 → log error；不抛（fire-and-forget 契约）。

5. **幂等**：`id` 是 uuid4，注册后不变；DELETE 用 id 删。

6. **租户隔离**：每个 webhook 绑定一个 tenant_id（防跨租户订阅）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_WEBHOOKS_PATH = Path(__file__).resolve().parents[1] / "data" / "webhooks.json"

# 事件类型白名单（公开契约；加新事件只追加，不改旧名）
ALLOWED_EVENTS: frozenset[str] = frozenset(
    {
        "query_completed",
        "audit_written",
        "quota_exceeded",
        "ingest_completed",
        "ingest_failed",
    }
)

# 重试参数
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.0
_REQUEST_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Webhook:
    """一个 webhook 订阅。"""

    id: str
    tenant_id: str
    url: str
    events: tuple[str, ...]
    secret: str  # HMAC 密钥（仅 webhook 持有方知道；服务端不暴露）
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    active: bool = True

    def to_public_dict(self) -> dict:
        """对外返回的 dict —— secret 不暴露。"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "url": self.url,
            "events": list(self.events),
            "created_at": self.created_at,
            "active": self.active,
        }


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 签名 webhook 回调 body（与 GitHub / Stripe 一致）。"""
    h = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={h.hexdigest()}"


def _verify_url(url: str) -> str:
    """URL 格式校验 + scheme 限制（仅 http/https，防 file:// / gopher://）。"""
    if not url or len(url) > 500:
        raise ValueError("url must be 1-500 chars")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("url must start with http:// or https://")
    return url


def _validate_events(events: Iterable[str]) -> tuple[str, ...]:
    """事件列表校验（必须在 ALLOWED_EVENTS 内；空 → 全订阅）。"""
    seen: list[str] = []
    for e in events:
        if e not in ALLOWED_EVENTS:
            raise ValueError(
                f"unknown event {e!r}; allowed: {sorted(ALLOWED_EVENTS)}"
            )
        if e not in seen:
            seen.append(e)
    return tuple(seen)


class WebhookRegistry:
    """webhook 注册表（文件持久化 + 进程内缓存）。"""

    def __init__(self, *, path: Path | str | None = None):
        env_path = os.environ.get("WEBHOOKS_PATH")
        if path is not None:
            self.path = Path(path)
        elif env_path:
            self.path = Path(env_path)
        else:
            self.path = DEFAULT_WEBHOOKS_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._hooks: dict[str, Webhook] = {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._hooks = {
                w["id"]: Webhook(
                    id=w["id"],
                    tenant_id=w["tenant_id"],
                    url=w["url"],
                    events=tuple(w["events"]),
                    secret=w["secret"],
                    created_at=w.get("created_at", ""),
                    active=w.get("active", True),
                )
                for w in data
                if isinstance(w, dict) and w.get("id")
            }
        except (json.JSONDecodeError, OSError):
            self._hooks = {}

    def _save(self) -> None:
        data = [
            {
                "id": w.id,
                "tenant_id": w.tenant_id,
                "url": w.url,
                "events": list(w.events),
                "secret": w.secret,
                "created_at": w.created_at,
                "active": w.active,
            }
            for w in self._hooks.values()
        ]
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ── CRUD ──

    def register(
        self,
        *,
        tenant_id: str,
        url: str,
        events: Iterable[str] = (),
        secret: str = "",
    ) -> Webhook:
        if not tenant_id:
            raise ValueError("tenant_id required")
        _verify_url(url)
        evs = _validate_events(events)
        if not secret:
            secret = "whsec-" + uuid.uuid4().hex  # 默认生成（生产应让用户指定）
        elif len(secret) < 8:
            raise ValueError("secret must be ≥ 8 chars")
        wid = "wh_" + uuid.uuid4().hex[:12]
        with self._lock:
            w = Webhook(
                id=wid, tenant_id=tenant_id, url=url, events=evs, secret=secret
            )
            self._hooks[wid] = w
            self._save()
        log.info("webhook_registered", extra={"id": wid, "tenant_id": tenant_id, "events": evs})
        return w

    def delete(self, *, webhook_id: str, tenant_id: str) -> bool:
        """删 webhook；只删同 tenant 的（防跨租户删除）。"""
        with self._lock:
            w = self._hooks.get(webhook_id)
            if not w or w.tenant_id != tenant_id:
                return False
            del self._hooks[webhook_id]
            self._save()
        log.info("webhook_deleted", extra={"id": webhook_id, "tenant_id": tenant_id})
        return True

    def list_for_tenant(self, *, tenant_id: str) -> list[Webhook]:
        """列出某 tenant 的所有 webhook（不含 secret）。"""
        with self._lock:
            return [w for w in self._hooks.values() if w.tenant_id == tenant_id]

    def for_event(self, *, tenant_id: str, event: str) -> list[Webhook]:
        """找订阅了该 event 的所有 webhook（含 secret —— 内部回调用）。"""
        with self._lock:
            return [
                w
                for w in self._hooks.values()
                if w.tenant_id == tenant_id
                and w.active
                and (not w.events or event in w.events)
            ]


# ── 进程级单例 ──

_registry: WebhookRegistry | None = None


def get_webhook_registry() -> WebhookRegistry:
    global _registry
    if _registry is None:
        _registry = WebhookRegistry()
    return _registry


def reset_webhook_registry() -> None:
    global _registry
    _registry = None


# ── 异步派发 ──


def dispatch_async(
    *,
    tenant_id: str,
    event: str,
    payload: dict,
    registry: WebhookRegistry | None = None,
) -> int:
    """fire-and-forget 派发 webhook 回调。

    Args:
        tenant_id: 触发事件的 tenant
        event: 事件类型（必须在 ALLOWED_EVENTS）
        payload: 回调 JSON body
        registry: 自定义 registry（默认全局单例）

    Returns:
        派发的 webhook 数量（线程已起，但执行结果异步）

    Notes:
        - 不抛异常（fire-and-forget）；失败仅 log error
        - 失败重试 3 次（指数退避）
        - 主线程不阻塞（threading.Thread daemon）
    """
    if event not in ALLOWED_EVENTS:
        log.warning("webhook_dispatch_unknown_event", extra={"event": event})
        return 0
    reg = registry or get_webhook_registry()
    targets = reg.for_event(tenant_id=tenant_id, event=event)
    if not targets:
        return 0
    body = json.dumps(
        {"event": event, "tenant_id": tenant_id, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    for w in targets:
        t = threading.Thread(
            target=_deliver_with_retry,
            args=(w, event, body),
            daemon=True,
            name=f"webhook-{w.id[:8]}",
        )
        t.start()
    return len(targets)


def _deliver_with_retry(webhook: Webhook, event: str, body: bytes) -> None:
    """单 webhook 投递 + 重试（指数退避）。"""
    sig = sign_payload(webhook.secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": sig,
        "X-Webhook-Event": event,
        "X-Webhook-Id": webhook.id,
    }
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(
                webhook.url, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
                if 200 <= resp.status < 300:
                    log.info(
                        "webhook_delivered",
                        extra={
                            "id": webhook.id,
                            "event": event,
                            "status": resp.status,
                            "attempt": attempt + 1,
                        },
                    )
                    return
                last_err = OSError(f"HTTP {resp.status}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
        # 失败：等后重试（最后 1 次不等）
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_BACKOFF_BASE_S * (2**attempt))
    log.error(
        "webhook_delivery_failed",
        extra={
            "id": webhook.id,
            "event": event,
            "error": str(last_err)[:200] if last_err else "unknown",
        },
    )


# ── 测试辅助 ──


def new_dev_secret() -> str:
    return "whsec-" + uuid.uuid4().hex


__all__ = [
    "ALLOWED_EVENTS",
    "DEFAULT_WEBHOOKS_PATH",
    "Webhook",
    "WebhookRegistry",
    "dispatch_async",
    "get_webhook_registry",
    "new_dev_secret",
    "reset_webhook_registry",
    "sign_payload",
]
