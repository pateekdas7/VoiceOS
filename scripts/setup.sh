#!/usr/bin/env bash
# scripts/setup.sh — One-shot developer environment setup for VoiceOS v2.
#
# Installs Python dev dependencies, pre-commit hooks, and verifies tooling.
# Run once after cloning the repository.
#
# Usage:
#   bash scripts/setup.sh
#
# Architecture: V6 Ch6 (Engineering Workflow); DocSuite-12.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[setup] $*"; }
err() { echo "[setup] ERROR: $*" >&2; exit 1; }

cd "$PROJECT_ROOT"

log "VoiceOS v2 — Developer Environment Setup"
log "Project root: $PROJECT_ROOT"

# ---------------------------------------------------------------------------
# Verify Python version
# ---------------------------------------------------------------------------
PYTHON="${PYTHON:-python3}"
PYTHON_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

log "Python version: $PYTHON_VERSION"
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]; }; then
    err "Python 3.11+ required. Got: $PYTHON_VERSION"
fi

# ---------------------------------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------------------------------
log "Installing Python package and dev dependencies..."
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -e ".[dev]"
log "Python deps: OK"

# ---------------------------------------------------------------------------
# Install pre-commit hooks
# ---------------------------------------------------------------------------
log "Installing pre-commit hooks..."
"$PYTHON" -m pre_commit install --install-hooks
log "Pre-commit hooks: OK"

# ---------------------------------------------------------------------------
# Verify tools
# ---------------------------------------------------------------------------
log "Verifying ruff..."
"$PYTHON" -m ruff --version
log "Verifying mypy..."
"$PYTHON" -m mypy --version
log "Verifying pytest..."
"$PYTHON" -m pytest --version

# ---------------------------------------------------------------------------
# Run a quick sanity check
# ---------------------------------------------------------------------------
log "Running ruff check on src/ ..."
"$PYTHON" -m ruff check src/
log "Running mypy --strict on src/ ..."
"$PYTHON" -m mypy --strict src/

log ""
log "=== Setup complete ==="
log "Run 'bash scripts/dev-up.sh' to start Docker services."
log "Run 'bash scripts/run-tests.sh' to execute the full test suite."
