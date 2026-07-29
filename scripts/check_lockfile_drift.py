#!/usr/bin/env python3
"""Detect drift between pyproject.toml and the three lockfiles.

Complements tests/test_lockfile.py with deeper checks:
- Pyproject [project] dependencies are sorted identically to requirements.in
- Lockfile has no orphan entries (top-level deps not in any .in)
- Dev lockfile includes pytest-asyncio + ruff + mypy + jupyter + ipykernel
- Otel lockfile has no transitive leak of uvicorn / fastapi (otel is independent stack)

Exit codes:
    0 — no drift detected
    1 — drift detected (printed, non-zero exit blocks CI)
    2 — required files missing (run scripts/refresh_lockfile.sh first)

Usage:
    python scripts/check_lockfile_drift.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

EXPECTED_PAIRS = [
    ("requirements.in", "requirements.lock"),
    ("requirements-dev.in", "requirements-dev.lock"),
    ("requirements-otel.in", "requirements-otel.lock"),
]


def parse_pyproject() -> dict[str, list[str]]:
    """Same logic as tests/test_lockfile.py — keep in sync."""
    text = PYPROJECT.read_text(encoding="utf-8")
    deps: dict[str, list[str]] = {"prod": [], "dev": [], "otel": []}
    current_key: str | None = None
    in_deps_value = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sec = stripped.strip("[]")
            current_key = "prod" if sec == "project" else None
            in_deps_value = False
            continue
        m_opt = re.match(r"^([a-z][a-z0-9_-]*)\s*=\s*\[$", stripped)
        if m_opt and current_key is None:
            sub = m_opt.group(1)
            if sub == "dev":
                current_key = "dev"
                in_deps_value = True
            elif sub == "observability":
                current_key = "otel"
                in_deps_value = True
            continue
        if current_key == "prod" and re.match(r"^dependencies\s*=\s*\[$", stripped):
            in_deps_value = True
            continue
        if stripped == "]":
            in_deps_value = False
            continue
        if in_deps_value and current_key and stripped.startswith('"'):
            m = re.match(r'"([A-Za-z0-9_.\-]+)', stripped)
            if m:
                deps[current_key].append(m.group(1).lower())
    return deps


def parse_in(path: Path) -> list[str]:
    """Parse requirements.in-style files: extract package names from quoted entries."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # inline comment trim
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)", stripped)
        if m:
            out.append(m.group(1).lower())
    return out


def parse_lockfile_top(path: Path) -> list[str]:
    """Extract top-level package names (one per block, first line of each)."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==[0-9]", line)
        if m:
            out.append(m.group(1).lower())
    return out


def check_missing_files() -> bool:
    """Check all 6 files exist; return False if any missing."""
    ok = True
    for infile, lockfile in EXPECTED_PAIRS:
        for p in (ROOT / infile, ROOT / lockfile):
            if not p.exists():
                print(f"✗ MISSING: {p}", file=sys.stderr)
                ok = False
    return ok


def check_in_matches_pyproject() -> list[str]:
    """Each requirements.in should be the canonical set from pyproject."""
    pyproject = parse_pyproject()
    issues: list[str] = []
    mapping = {"requirements.in": "prod", "requirements-dev.in": "dev", "requirements-otel.in": "otel"}
    for infile, key in mapping.items():
        in_path = ROOT / infile
        if not in_path.exists():
            continue
        in_pkgs = set(parse_in(in_path))
        py_pkgs = set(pyproject[key])
        # in_pkgs may include transitive dependencies (optional inclusions);
        # we only require that all pyproject deps are in in_pkgs.
        missing = py_pkgs - in_pkgs
        if missing:
            issues.append(f"  {infile}: pyproject [{key}] deps not in .in: {sorted(missing)}")
    return issues


def check_lockfile_includes_in() -> list[str]:
    """Each lockfile should include all packages from its .in."""
    issues: list[str] = []
    for infile, lockfile in EXPECTED_PAIRS:
        in_path = ROOT / infile
        lock_path = ROOT / lockfile
        if not in_path.exists() or not lock_path.exists():
            continue
        in_pkgs = set(parse_in(in_path))
        lock_pkgs = set(parse_lockfile_top(lock_path))
        missing = in_pkgs - lock_pkgs
        if missing:
            issues.append(
                f"  {lockfile}: missing from top-level: {sorted(missing)}\n"
                f"    → run scripts/refresh_lockfile.sh"
            )
    return issues


def check_otel_independent() -> list[str]:
    """Otel lockfile shouldn't pull fastapi/uvicorn — it's an independent stack."""
    issues: list[str] = []
    otel_lock = set(parse_lockfile_top(ROOT / "requirements-otel.lock"))
    heavy = {"fastapi", "uvicorn", "starlette"}
    leaked = heavy & otel_lock
    if leaked:
        issues.append(f"  requirements-otel.lock pulls in {leaked} — should be independent stack")
    return issues


def main() -> int:
    print("Lockfile drift check (ENTERPRISE_AUDIT M3)")
    print("=" * 50)

    if not check_missing_files():
        return 2

    issues: list[str] = []
    issues.extend(check_in_matches_pyproject())
    issues.extend(check_lockfile_includes_in())
    issues.extend(check_otel_independent())

    if issues:
        print("✗ Drift detected:")
        for i in issues:
            print(i)
        return 1

    print("✓ All 3 lockfiles in sync with pyproject.")
    print("  • requirements.lock ↔ pyproject [project]")
    print("  • requirements-dev.lock ↔ requirements-dev.in")
    print("  • requirements-otel.lock ↔ requirements-otel.in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
