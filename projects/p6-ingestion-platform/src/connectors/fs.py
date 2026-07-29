"""文件系统连接器 —— 共享盘 / NAS 的最小真实形态。

对比原 P1 `_load_corpus`（`glob("*.md")` + 立即 read_text + 手搓 frontmatter）：
- 这里 list 阶段**只 stat 不读内容** → etag 用 (mtime, size) 合成，改没改一眼看出
- provenance 从 frontmatter 抽（author/owner、sensitivity、version）
- 支持多后缀，交给 P5 loaders 按 source_format 分派
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from ..models import SourceRecord
from . import Connector, frontmatter_provenance

# 后缀 → source_format（与 P5 SourceFormat 对齐）
_EXT_FORMAT = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
}


class FileSystemConnector(Connector):
    """扫描一个目录树。生产上把 root 换成挂载的共享盘即可。"""

    name = "fs"

    def __init__(
        self,
        root: Path | str,
        *,
        patterns: tuple[str, ...] = ("*.md", "*.txt", "*.pdf", "*.docx", "*.xlsx", "*.html"),
        read_frontmatter: bool = True,
    ):
        self.root = Path(root).resolve()
        self.patterns = patterns
        self.read_frontmatter = read_frontmatter
        self._max_mtime = 0.0

    def list_records(self, since_cursor: str = "") -> Iterator[SourceRecord]:
        since = float(since_cursor) if since_cursor else 0.0
        for pat in self.patterns:
            for p in sorted(self.root.rglob(pat)):
                if not p.is_file():
                    continue
                st = p.stat()
                self._max_mtime = max(self._max_mtime, st.st_mtime)
                if since and st.st_mtime <= since:
                    continue  # 增量模式：mtime 没超过游标就跳过
                yield self._to_record(p, st)

    def _to_record(self, p: Path, st) -> SourceRecord:
        rel = p.relative_to(self.root).as_posix()
        fmt = _EXT_FORMAT.get(p.suffix.lower(), "txt")
        author = ""
        sensitivity = "public"
        version = ""
        acl: tuple[str, ...] = ()
        # 只对文本类读 frontmatter（二进制格式没有）
        if self.read_frontmatter and fmt in ("md", "txt"):
            try:
                meta, _ = frontmatter_provenance(p.read_text(encoding="utf-8", errors="replace"))
                author = meta.get("owner") or meta.get("author", "")
                sensitivity = meta.get("sensitivity", "public")
                version = meta.get("version", "")
                if meta.get("acl"):
                    acl = tuple(x.strip() for x in meta["acl"].split(",") if x.strip())
            except OSError:
                pass
        return SourceRecord(
            source_id=f"{self.name}:{rel}",
            connector=self.name,
            uri=p.as_uri(),
            source_format=fmt,
            # etag = mtime+size：不读正文就能判断"变没变"
            etag=f"{st.st_mtime_ns}:{st.st_size}",
            mtime=st.st_mtime,
            author=author,
            acl_principals=acl,
            sensitivity=sensitivity,
            version=version,
            extra={"rel_path": rel, "bytes": st.st_size},
        )

    def fetch_body(self, rec: SourceRecord) -> bytes:
        p = self.root / rec.extra["rel_path"]
        return p.read_bytes()

    def next_cursor(self) -> str:
        return str(self._max_mtime) if self._max_mtime else ""
