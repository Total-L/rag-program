"""对象存储连接器 —— S3 / GCS / Azure Blob / MinIO。

企业里非结构化文档（PDF 合同、报表、扫描件）绝大多数躺在对象存储里。

两个后端，同一接口：
- `backend="s3"`：真实 boto3。传 `endpoint_url` 就能指向 MinIO 或任何 S3 兼容存储。
  ETag 直接用 S3 自己的（免费的版本标识，天生适合做增量）。
- `backend="local"`：本地目录模拟对象列举（key/etag/last_modified 三件套齐全）。

⚠️ 本机没有 MinIO 也没有 AWS 凭证，所以默认 `local`。
   s3 分支的调用方式（`list_objects_v2` 分页 + `get_object`）是真实写法，
   但**本机未做真实 S3/MinIO 验证**。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from ..models import SourceRecord
from . import Connector

_EXT_FORMAT = {
    ".md": "md",
    ".txt": "txt",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
}


class ObjectStoreConnector(Connector):
    """列举 + 拉取对象存储里的文档。"""

    name = "objectstore"

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "",
        backend: str = "local",
        local_root: Path | str | None = None,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
    ):
        self.bucket = bucket
        self.prefix = prefix
        self.backend = backend
        self.endpoint_url = endpoint_url
        self.region = region
        self.local_root = Path(local_root).resolve() if local_root else None
        self._client = None
        self._latest = ""

        if backend == "local" and not self.local_root:
            raise ValueError("backend='local' 需要传 local_root")
        if backend not in ("local", "s3"):
            raise ValueError(f"未知 backend: {backend!r}（local / s3）")

    # ── S3 客户端（延迟创建）─────────────────────────
    def _s3(self):
        if self._client is None:
            import boto3  # 已装

            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint_url, region_name=self.region
            )
        return self._client

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        if self.backend == "s3":
            yield from self._list_s3(since_cursor)
        else:
            yield from self._list_local(since_cursor)

    def _list_s3(self, since_cursor: str) -> Iterator[SourceRecord]:
        """真实 S3 列举：分页器 + ETag 做增量。"""
        paginator = self._s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                lm = obj["LastModified"].isoformat()
                if lm > self._latest:
                    self._latest = lm
                if since_cursor and lm <= since_cursor:
                    continue
                yield self._record(
                    key=key,
                    etag=str(obj.get("ETag", "")).strip('"'),
                    mtime=obj["LastModified"].timestamp(),
                    size=int(obj.get("Size", 0)),
                )

    def _list_local(self, since_cursor: str) -> Iterator[SourceRecord]:
        """本地目录模拟对象存储：etag 用内容 md5（与 S3 单段上传的 ETag 语义一致）。"""
        assert self.local_root is not None
        base = self.local_root / self.prefix if self.prefix else self.local_root
        if not base.exists():
            return
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            st = p.stat()
            lm = str(st.st_mtime)
            if lm > self._latest:
                self._latest = lm
            if since_cursor and float(lm) <= float(since_cursor):
                continue
            etag = hashlib.md5(p.read_bytes()).hexdigest()  # noqa: S324 - 模拟 S3 ETag，非安全用途
            yield self._record(
                key=p.relative_to(self.local_root).as_posix(),
                etag=etag,
                mtime=st.st_mtime,
                size=st.st_size,
            )

    def _record(self, *, key: str, etag: str, mtime: float, size: int) -> SourceRecord:
        fmt = _EXT_FORMAT.get(Path(key).suffix.lower(), "txt")
        return SourceRecord(
            source_id=f"{self.name}:{self.bucket}/{key}",
            connector=self.name,
            uri=f"s3://{self.bucket}/{key}",
            source_format=fmt,
            etag=etag,
            mtime=mtime,
            sensitivity="internal",  # 对象存储通常无 frontmatter，默认内部，由 PII 阶段再分级
            extra={"key": key, "bytes": size, "backend": self.backend},
        )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        key = rec.extra["key"]
        if self.backend == "s3":
            return self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
        assert self.local_root is not None
        return (self.local_root / key).read_bytes()

    def next_cursor(self) -> str:
        return self._latest
