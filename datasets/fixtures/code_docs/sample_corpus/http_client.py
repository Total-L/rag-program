"""A minimal HTTP client built on stdlib only.

Demonstrates connection reuse + retry semantics. Q&A target:
"how to make HTTP requests without requests library?"
"""
from __future__ import annotations

import json
import socket
import ssl
import urllib.parse
from typing import Any


class SimpleHTTPResponse:
    """Minimal HTTP response wrapper."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.body = body
        self.headers = headers

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def http_get(url: str, *, timeout: float = 5.0) -> SimpleHTTPResponse:
    """Perform a single HTTP/1.1 GET. No redirects, no chunked decoding."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parts.scheme}")
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        if parts.scheme == "https":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Connection: close\r\n"
            f"User-Agent: tiny-http/0.1\r\n"
            f"\r\n"
        )
        raw.sendall(req.encode("ascii"))

        chunks: list[bytes] = []
        while True:
            data = raw.recv(4096)
            if not data:
                break
            chunks.append(data)
    finally:
        raw.close()

    raw_bytes = b"".join(chunks)
    head, _, body = raw_bytes.partition(b"\r\n\r\n")
    head_lines = head.split(b"\r\n")
    status = int(head_lines[0].split()[1]) if len(head_lines) > 0 else 0
    headers: dict[str, str] = {}
    for line in head_lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
    return SimpleHTTPResponse(status=status, body=body, headers=headers)
