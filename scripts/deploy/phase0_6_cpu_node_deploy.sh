#!/usr/bin/env bash
# Phases 10-11 — Deploy Phase 0-6 monitoring work to the CPU node.
#
# Prereqs:
#   - SSH to acer@159.223.110.159:64963 reachable (fallback 50401)
#   - ~/.ssh/cpu_node private key present on this host
#   - Node already has /opt/voiceos/app checked out and tracked
#
# What this does:
#   1. rsync the Phase 0-6 source changes into /opt/voiceos/app
#   2. Install systemd units added in Phase 5 / G7
#   3. Reload systemd + restart affected services
#   4. Run the three new CI gates on the node (alertmanager drift,
#      runbook coverage, dashboard generator --check)
#   5. Run the Phase 1 + Phase 2 + Phase 3 regression tests
#   6. Print a short post-deploy report (service status, metric counts)

set -euo pipefail

SSH_HOST="${SSH_HOST:-acer@159.223.110.159}"
SSH_PORT="${SSH_PORT:-64963}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/cpu_node}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/voiceos/app}"

SSH="ssh -i $SSH_KEY -p $SSH_PORT -o StrictHostKeyChecking=no"
RSYNC="rsync -e \"$SSH\" -az --delete"

say() { printf "\033[1;36m[deploy]\033[0m %s\n" "$*"; }

say "1/6 — reachability"
$SSH "$SSH_HOST" "echo node=\$(hostname) py=\$(python3 --version)"

say "2/6 — rsync Phase 0-6 artifacts"
eval $RSYNC \
    monitoring/ \
    scripts/ \
    src/libs/observability/metrics.py \
    src/services/web_api/api.py \
    src/services/web_api/main.py \
    src/services/ops_intelligence/plumbing/metrics.py \
    monitoring/gpu_fleet/metrics.py \
    docs/schema/ \
    docs/runbooks/alerts/ \
    MONITORING_BASELINE.md \
    MONITORING_DEPENDENCY_MAP.md \
    MONITORING_GAP_REGISTER.md \
    CHANGELOG.md \
    "$SSH_HOST:$REMOTE_ROOT/" || true

say "3/6 — install systemd units (G7)"
$SSH "$SSH_HOST" "sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-scheduler.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-prometheus.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-grafana.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-loki.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-promtail.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-jaeger.service /etc/systemd/system/ && \
                  sudo -n cp $REMOTE_ROOT/scripts/systemd/voiceos-alertmanager.service /etc/systemd/system/ && \
                  sudo -n systemctl daemon-reload"

say "4/6 — restart affected services (bff + webapi + scheduler)"
$SSH "$SSH_HOST" "sudo -n systemctl restart voiceos-webapi.service voiceos-bff.service || true; \
                  sudo -n systemctl enable --now voiceos-scheduler.service || true"

say "5/6 — run CI gates on the node"
$SSH "$SSH_HOST" "cd $REMOTE_ROOT && python3 scripts/ci/alertmanager_config_drift.py && \
                                      python3 scripts/ci/check_runbook_coverage.py && \
                                      python3 scripts/grafana/generate_dashboards.py --check"

say "6/6 — run Phase 1-3 regression tests"
$SSH "$SSH_HOST" "cd $REMOTE_ROOT && python3 -m pytest -q \
    tests/unit/monitoring/test_phase1_scheduler_metrics.py \
    tests/unit/web_api/test_phase2_route_order.py \
    tests/unit/monitoring/test_phase3_registry_and_cardinality.py"

say "post-deploy: service status + metric sample"
$SSH "$SSH_HOST" "systemctl --no-pager status voiceos-scheduler.service | head -10; \
                  curl -s http://localhost:9108/metrics | grep -E 'voiceos_(gpu_fleet_poller|escalation_scheduler|startup_handlers)' | head"

say "DONE — Phase 0-6 deployed."
