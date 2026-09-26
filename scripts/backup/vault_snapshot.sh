#!/usr/bin/env bash
# Scheduled wrapper for the current Vault file-backend backup implementation.
set -euo pipefail
exec /bin/bash /opt/voiceos/app/infra/dr/scripts/backup-vault.sh --seal
