#!/usr/bin/env bash
# scripts/lint.sh — Run ruff (lint + format check) and mypy --strict.
#
# Usage:
#   bash scripts/lint.sh            # check only
#   bash scripts/lint.sh --fix      # apply ruff auto-fixes
#
# Architecture: V6 Ch9; DocSuite-12.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[lint] $*"; }

cd "$PROJECT_ROOT"

FIX=false
for arg in "$@"; do
    case "$arg" in
        --fix) FIX=true ;;
    esac
done

if [ "$FIX" = "true" ]; then
    log "Applying ruff fixes..."
    python -m ruff check --fix src/ tests/
    python -m ruff format src/ tests/
else
    log "Running ruff check..."
    python -m ruff check src/ tests/
    log "Running ruff format check..."
    python -m ruff format --check src/ tests/
fi

log "Running mypy --strict..."
python -m mypy --strict src/ tests/

log ""
log "=== Lint passed ==="
