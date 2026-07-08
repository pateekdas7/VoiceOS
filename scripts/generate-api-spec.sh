#!/usr/bin/env bash
# scripts/generate-api-spec.sh — Generate OpenAPI 3.1 specification (stub).
#
# Implemented in Sprint-025 (Admin Portal, AI Configuration & Integration
# Platform). This stub is a placeholder that exits cleanly so CI pipelines
# can reference it without failing.
#
# When Sprint-025 is complete this script will:
#   1. Run the FastAPI/Pydantic spec generator against src/services/api-platform/
#   2. Write openapi.json to docs/api/
#   3. Validate the generated spec with spectral or redocly
#
# Architecture: V5 Ch15 (Public REST API); DocSuite-04.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo "[generate-api-spec] $*"; }

cd "$PROJECT_ROOT"

log "API spec generation is a stub — implemented in Sprint-025."
log "Target: docs/api/openapi.json"
log "Skipping (Sprint-025 not yet implemented)."

exit 0
