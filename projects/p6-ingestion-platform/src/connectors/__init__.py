"""P6 连接器层 —— "怎么提取企业级数据"的答案。

设计原则（两阶段协议，这是能扩到百万文档的关键）：

    list_records()  →  只返回元数据（source_id / etag / mtime / author / acl）
                       便宜、可全量扫、不下载正文
    fetch_body(rec) →  只对 new/changed 的记录下载正文
                       贵，所以要靠 manifest 的 diff 把量压到最小

如果把两步合成一步（"列举即下载"），日常增量扫描就等于全量下载 —— 这是
原 P1 `_load_corpus` 的做法（`glob("*.md")` + 立即 `read_text()`），
文档量一上来就没法跑。

四个连接器覆盖企业四类典型数据源：

| 连接器      | 企业对应              | 本机替身                | 换生产怎么做              |
|-------------|-----------------------|-------------------------|---------------------------|
| `fs`        | 共享盘 / NAS          | 本地目录（真实可跑）    | 换挂载路径即可            |
| `sql`       | Postgres / MySQL 业务库| SQLite（真实 SQL 可跑） | 只换 DSN，SQL 不变        |
| `objectstore`| S3 / GCS / Azure Blob | 本地目录模拟对象列举    | 传 endpoint_url 指向 MinIO/S3 |
| `confluence`| Confluence / SharePoint| 录制的 JSON fixture 回放| 传 base_url + token       |

⚠️ 诚实标注：本机无 MinIO / 无真实 Confluence，故 objectstore 默认走 `local` 后端、
   confluence 走 fixture 回放。两者的**接口与分页/游标逻辑是真实的**，
   但"连真实云/SaaS"这条路径在本机**未验证**。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import SourceRecord


class Connector(ABC):
    """所有连接器的统一契约。"""

    name: str = "abstract"

    @abstractmethod
    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        """列举源侧记录（**不含正文**）。

        `since_cursor` 非空时应尽量只返回该游标之后变化的记录（增量模式）；
        源侧不支持游标就忽略它并全量列举（全量模式）。
        """

    @abstractmethod
    def fetch_body(self, rec: SourceRecord) -> bytes:
        """拉取单条记录的正文字节。只对 new/changed 调用。"""

    def next_cursor(self) -> str:
        """本轮扫描后应保存的新游标。不支持游标的源返回空串。"""
        return ""

    def supports_full_scan(self) -> bool:
        """能否全量列举 —— 决定 manifest 是否允许判定"删除"。

        游标型增量源（如只能拉"最近变更"的 API）返回 False，
        这样 `ManifestStore.diff(full_scan=False)` 就不会误删数据。
        """
        return True


def frontmatter_provenance(text: str) -> tuple[dict[str, str], str]:
    """解析 Markdown frontmatter，抽出 provenance，并返回去掉 frontmatter 的正文。

    复用本仓库既有企业 fixture 的格式（`datasets/fixtures/enterprise/*.md`
    带 `sensitivity:` / `owner:`），把原来 P1 里手搓的那段解析逻辑收敛到一处。
    """
    meta: dict[str, str] = {}
    if not text.startswith("---\n"):
        return meta, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return meta, text
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5 :]


from .confluence import ConfluenceConnector  # noqa: E402
from .fs import FileSystemConnector  # noqa: E402
from .objectstore import ObjectStoreConnector  # noqa: E402
from .sql import SQLConnector  # noqa: E402

__all__ = [
    "Connector",
    "frontmatter_provenance",
    "FileSystemConnector",
    "SQLConnector",
    "ObjectStoreConnector",
    "ConfluenceConnector",
]
