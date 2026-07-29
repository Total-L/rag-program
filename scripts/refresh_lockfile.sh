#!/usr/bin/env bash
# Regenerate all lockfiles from sources of truth.
# Sources of truth (in priority order):
#   1. pyproject.toml [project] dependencies          → requirements.lock
#   2. requirements-dev.in (mirrors [optional].dev)   → requirements-dev.lock
#   3. requirements-otel.in (mirrors [optional].observability) → requirements-otel.lock
#
# Usage:
#   bash scripts/refresh_lockfile.sh
#
# Exits 0 if all three lockfiles regenerated and tests pass; 1 otherwise.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "════════════════════════════════════════════"
echo "  Lockfile refresh (ENTERPRISE_AUDIT M3)"
echo "════════════════════════════════════════════"

# 1. prod — read pyproject directly via uv pip compile
#    (pyproject is not directly supported, so we use the canonical requirements.in)
echo "→ requirements.lock (prod) ..."
uv pip compile requirements.in \
    --quiet \
    --no-header \
    --strip-extras \
    --generate-hashes \
    --output-file=requirements.lock

# 2. dev
echo "→ requirements-dev.lock ..."
uv pip compile requirements-dev.in \
    --quiet \
    --no-header \
    --strip-extras \
    --generate-hashes \
    --output-file=requirements-dev.lock

# 3. otel
echo "→ requirements-otel.lock ..."
uv pip compile requirements-otel.in \
    --quiet \
    --no-header \
    --strip-extras \
    --generate-hashes \
    --output-file=requirements-otel.lock

echo
echo "✓ All three lockfiles regenerated."
echo
echo "→ Running drift tests ..."
python -m pytest tests/test_lockfile.py -q

echo
echo "✓ Done. Don't forget to:"
echo "    git add requirements*.lock requirements*.in"
echo "    git commit -m 'chore: refresh lockfiles'"