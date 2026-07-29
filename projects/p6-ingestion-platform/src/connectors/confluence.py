"""Confluence / SharePoint 类 SaaS 连接器 —— 企业知识库的真正所在地。

SaaS 源和文件/对象存储有三个本质差别，必须单独处理：

1. **分页是游标式的**，不是偏移式的（`_links.next` / `start+limit`）
2. **有原生版本号**（`version.number`）→ 最理想的 change_key，比内容哈希还便宜
3. **不能可靠全量列举**（API 限流、权限可见性差异）→ 因此
   `supports_full_scan()` 返回 False，禁止 manifest 据此判"删除"。
   这是防误删的关键：SaaS 拉不到 ≠ 被删除，可能只是限流或没权限。

⚠️ 本机无真实 Confluence 实例与 token，故用**录制 fixture 回放**（`fixture_path`）。
   分页/游标/版本号逻辑是真实的；`_http_list` 分支**未做真实 API 验证**。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..models import SourceRecord
from . import Connector


class ConfluenceConnector(Connector):
    """Confluence Cloud REST v1 形态。

    Args:
        base_url: 如 https://acme.atlassian.net/wiki（生产）
        token: API token（生产）
        space: 限定空间
        fixture_path: 传了就走本地 fixture 回放（本机验证用）
    """

    name = "confluence"

    def __init__(
        self,
        *,
        base_url: str = "",
        token: str = "",
        space: str = "",
        fixture_path: Path | str | None = None,
        limit: int = 50,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.space = space
        self.limit = limit
        self.fixture_path = Path(fixture_path) if fixture_path else None
        self._bodies: dict[str, str] = {}
        self._max_version = 0
        if not self.fixture_path and not self.base_url:
            raise ValueError("需要 base_url（生产）或 fixture_path（本机回放）")

    def supports_full_scan(self) -> bool:
        # SaaS 列举不可靠 → 不允许据此判定删除（防误删真实数据）
        return False

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        pages = self._fixture_list() if self.fixture_path else self._http_list()
        for page in pages:
            pid = str(page["id"])
            version = int(page.get("version", {}).get("number", 1))
            self._max_version = max(self._max_version, version)
            body = page.get("body", {}).get("storage", {}).get("value", "") or page.get("body", "")
            self._bodies[pid] = body
            vby = page.get("version", {}).get("by", {})
            labels = page.get("metadata", {}).get("labels", {}).get("results", [])
            acl = tuple(
                entry["name"]
                for entry in labels
                if str(entry.get("name", "")).startswith("acl-")  # noqa: E741
            )
            yield SourceRecord(
                source_id=f"{self.name}:{pid}",
                connector=self.name,
                uri=f"{self.base_url}/pages/{pid}" if self.base_url else f"confluence://{pid}",
                source_format="html",  # Confluence storage format 本质是 XHTML → 复用 P5 html loader
                # 原生版本号是最好的 change_key：一个整数就够，不用下载正文
                etag=f"v{version}",
                mtime=float(page.get("version", {}).get("whenEpoch", 0) or 0),
                author=str(vby.get("displayName", "")),
                acl_principals=acl,
                sensitivity=self._sensitivity_from_labels(labels),
                version=str(version),
                extra={"page_id": pid, "space": page.get("space", {}).get("key", self.space)},
            )

    @staticmethod
    def _sensitivity_from_labels(labels: list[dict[str, Any]]) -> str:
        """Confluence label → 敏感度。企业里通常就是靠 label/标签体系做粗分级。"""
        names = {str(entry.get("name", "")).lower() for entry in labels}  # noqa: E741
        if "restricted" in names or "confidential" in names:
            return "restricted"
        if "public" in names:
            return "public"
        return "internal"

    def _fixture_list(self) -> list[dict[str, Any]]:
        assert self.fixture_path is not None
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return data.get("results", data if isinstance(data, list) else [])

    def _http_list(self) -> Iterator[dict[str, Any]]:  # pragma: no cover - 本机无实例
        """真实分页拉取。未在本机验证。"""
        import urllib.request

        start = 0
        while True:
            q = (
                f"{self.base_url}/rest/api/content?type=page&limit={self.limit}"
                f"&start={start}&expand=version,body.storage,space,metadata.labels"
            )
            if self.space:
                q += f"&spaceKey={self.space}"
            req = urllib.request.Request(
                q, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            results = payload.get("results", [])
            if not results:
                return
            yield from results
            if not payload.get("_links", {}).get("next"):
                return
            start += self.limit

    def fetch_body(self, rec: SourceRecord) -> bytes:
        pid = rec.extra["page_id"]
        if pid in self._bodies:
            return self._bodies[pid].encode("utf-8")
        raise KeyError(f"正文未缓存且无法回查: {rec.source_id}")

    def next_cursor(self) -> str:
        return str(self._max_version) if self._max_version else ""
