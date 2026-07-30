"""Sample CLI for the P2 demo corpus.

Demonstrates argparse + dispatch table pattern. Useful for AST-based
chunking because the dispatcher + handler pair is a common Q&A target.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_greet(args: argparse.Namespace) -> int:
    print(f"hello, {args.name}!")
    return 0


def _cmd_upper(args: argparse.Namespace) -> int:
    print(args.text.upper())
    return 0


def _cmd_file(args: argparse.Namespace) -> int:
    p = Path(args.path)
    if not p.exists():
        print(f"file not found: {p}", file=sys.stderr)
        return 2
    print(p.read_text(encoding="utf-8"))
    return 0


_HANDLERS = {
    "greet": _cmd_greet,
    "upper": _cmd_upper,
    "file": _cmd_file,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("greet")
    p1.add_argument("--name", default="world")

    p2 = sub.add_parser("upper")
    p2.add_argument("text")

    p3 = sub.add_parser("file")
    p3.add_argument("path")

    args = parser.parse_args(argv)
    return _HANDLERS[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
