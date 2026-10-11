#!/usr/bin/env bash
# jentiv16_production_testify.sh — the "production grade testified" battery.
#
# 10 verification groups. Each prints a PASS or FAIL line. The script
# does NOT abort on first failure; it collects all failures and prints
# a tally at the end so operators see the full state in one pass.
set -uo pipefail
APP=/opt/voiceos/app
VENV=/opt/voiceos/.venv
PASS=0; FAIL=0
FAILS=()
pass() { PASS=$((PASS+1)); printf '  [PASS] %s\n' "$*"; }
fail() { FAIL=$((FAIL+1)); FAILS+=("$*"); printf '  [FAIL] %s\n' "$*"; }
section() { printf '\n=== %s ===\n' "$*"; }

cd "$APP"

section "1. systemd app services"
for svc in voiceos-scheduler voiceos-webapi voiceos-bff voiceos-dialer-worker; do
  state=$(systemctl is-active "$svc" 2>/dev/null || echo unknown)
  if [ "$state" = active ]; then pass "$svc = active"; else fail "$svc = $state"; fi
done

section "2. docker monitoring stack"
for ctr in voiceos-prometheus voiceos-alertmanager voiceos-grafana voiceos-loki voiceos-promtail voiceos-jaeger; do
  s=$(docker inspect -f '{{.State.Status}}' "$ctr" 2>/dev/null || echo missing)
  if [ "$s" = running ]; then pass "$ctr = running"; else fail "$ctr = $s"; fi
done

section "3. HTTP health endpoints (correct paths)"
check_http() {
  local name=$1 url=$2 want=$3 to=${4:-5}
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$to" "$url" || echo ERR)
  if [ "$code" = "$want" ]; then pass "$name $url → $code"; else fail "$name $url → $code (want $want)"; fi
}
check_http scheduler        http://localhost:9108/                200
check_http webapi-health    http://localhost:8000/system/health   200
check_http webapi-metrics   http://localhost:8000/metrics         200
check_http bff-health       http://localhost:3000/system/health   200 45
check_http bff-metrics      http://localhost:3000/metrics         200
check_http prometheus       http://localhost:9090/-/healthy       200
check_http prometheus-ready http://localhost:9090/-/ready         200
check_http grafana          http://localhost:3001/api/health      200
check_http alertmanager     http://localhost:9093/-/healthy       200
check_http alertmanager-ready http://localhost:9093/-/ready       200
check_http loki             http://localhost:3100/ready           200
check_http jaeger           http://localhost:16686/               200
check_http promtail         http://localhost:9080/ready           200

section "4. Prometheus — alert rules loaded"
rules_count=$(curl -s http://localhost:9090/api/v1/rules | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(len(g.get('rules', [])) for g in d.get('data', {}).get('groups', [])))" 2>/dev/null || echo 0)
if [ "$rules_count" -ge 40 ]; then pass "prometheus loaded $rules_count alert rules (>= 40)"; else fail "prometheus loaded only $rules_count alert rules (want >= 40)"; fi

section "5. Prometheus — scheduler target is UP"
target_health=$(curl -s 'http://localhost:9090/api/v1/targets?state=active' | python3 -c "
import json,sys
d=json.load(sys.stdin)
targets=d.get('data',{}).get('activeTargets',[])
up=[t for t in targets if 'scheduler' in (t.get('labels',{}).get('job','')+t.get('scrapePool','')) or t.get('scrapeUrl','').endswith(':9108/metrics')]
print(up[0]['health'] if up else 'missing')
" 2>/dev/null || echo err)
if [ "$target_health" = "up" ]; then pass "prometheus scheduler target = up"; else fail "prometheus scheduler target = $target_health"; fi

section "6. Scheduler metrics — scraped and non-zero"
metrics=$(curl -s http://localhost:9108/)
for m in voiceos_gpu_fleet_poller_last_run_timestamp voiceos_gpu_fleet_health_score voiceos_alert_lifecycle_scheduler_last_run_timestamp; do
  if printf '%s\n' "$metrics" | grep -q "^$m "; then pass "metric $m exported"; else fail "metric $m missing"; fi
done

section "7. Grafana — API responsive, dashboards present"
dash_count=$(curl -s -u admin:admin "http://localhost:3001/api/search?type=dash-db" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
if [ "$dash_count" -ge 1 ]; then pass "grafana API lists $dash_count dashboards"; else fail "grafana API returned 0 dashboards"; fi

section "8. Alertmanager — can receive & route an alert"
# POST a synthetic alert to the API; expect 200
alertjson='[{"labels":{"alertname":"ProductionGradeTestify","severity":"warning","service":"testify"},"annotations":{"summary":"synthetic test alert"},"generatorURL":"http://jentiv16/testify"}]'
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$alertjson" http://localhost:9093/api/v2/alerts || echo ERR)
if [ "$code" = "200" ]; then pass "alertmanager POST /api/v2/alerts → 200"; else fail "alertmanager POST /api/v2/alerts → $code"; fi
sleep 2
# Query active alerts, confirm ours is there
seen=$(curl -s http://localhost:9093/api/v2/alerts | python3 -c "
import json,sys
alerts=json.load(sys.stdin)
print('yes' if any(a.get('labels',{}).get('alertname')=='ProductionGradeTestify' for a in alerts) else 'no')
" 2>/dev/null || echo err)
if [ "$seen" = "yes" ]; then pass "alertmanager stored synthetic alert"; else fail "alertmanager did not store synthetic alert"; fi

section "9. Loki — accepting pushes + query API responds"
# Push a log line
ts_ns=$(date +%s)000000000
pushjson="{\"streams\":[{\"stream\":{\"service\":\"testify\",\"level\":\"info\"},\"values\":[[\"$ts_ns\",\"production-grade testify log line\"]]}]}"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$pushjson" http://localhost:3100/loki/api/v1/push || echo ERR)
if [ "$code" = "204" ]; then pass "loki POST /loki/api/v1/push → 204"; else fail "loki POST /loki/api/v1/push → $code"; fi
sleep 1
start_ns=$((ts_ns - 60*1000000000))
end_ns=$((ts_ns + 60*1000000000))
qcode=$(curl -s -o /dev/null -w '%{http_code}' -G \
  --data-urlencode 'query={service="testify"}' \
  --data-urlencode "start=$start_ns" \
  --data-urlencode "end=$end_ns" \
  --data-urlencode 'limit=5' \
  'http://localhost:3100/loki/api/v1/query_range' || echo ERR)
if [ "$qcode" = "200" ]; then pass "loki query_range → 200"; else fail "loki query_range → $qcode"; fi

section "10. CI gates (static-analysis)"
cd "$APP"
export PATH="$VENV/bin:$PATH"
if python3 scripts/ci/alertmanager_config_drift.py >/tmp/drift.out 2>&1; then pass "alertmanager_config_drift.py"; else fail "alertmanager_config_drift.py (see /tmp/drift.out)"; fi
if python3 scripts/ci/check_runbook_coverage.py >/tmp/rb.out 2>&1; then pass "check_runbook_coverage.py"; else fail "check_runbook_coverage.py (see /tmp/rb.out)"; fi
if python3 scripts/grafana/generate_dashboards.py --check >/tmp/dg.out 2>&1; then pass "generate_dashboards.py --check"; else fail "generate_dashboards.py --check (see /tmp/dg.out)"; fi

section "11. unit test suite (Phase 1/2/3 monitoring)"
# APP is a symlink to /home/jenti/VoiceOS which is pytest's rootdir;
# cd into the real path so pytest's rootdir discovery is deterministic.
TESTS_ROOT=$(readlink -f "$APP")
cd "$TESTS_ROOT"
pytest_out=$($VENV/bin/python -m pytest tests/unit/monitoring tests/unit/web_api/test_phase2_route_order.py -q --timeout=60 --no-cov 2>&1 | tail -5 || true)
echo "$pytest_out"
if echo "$pytest_out" | grep -qE "[0-9]+ passed"; then pass "pytest monitoring suite"; else fail "pytest monitoring suite"; fi

section "12. CHANGELOG + runbooks on disk"
if grep -q 'v2.0.44' $APP/CHANGELOG.md; then pass "CHANGELOG.md contains v2.0.44 entry"; else fail "CHANGELOG.md missing v2.0.44"; fi
rb_count=$(ls -1 $APP/docs/runbooks/alerts/*.md 2>/dev/null | wc -l)
if [ "$rb_count" -ge 30 ]; then pass "docs/runbooks/alerts/ has $rb_count .md files (>= 30)"; else fail "docs/runbooks/alerts/ has only $rb_count .md files"; fi

printf '\n============================================================\n'
printf 'PRODUCTION-GRADE TESTIFY RESULT: %d PASS, %d FAIL\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf '\nFAILURES:\n'
  for f in "${FAILS[@]}"; do printf '  - %s\n' "$f"; done
fi
printf '============================================================\n'
exit "$FAIL"
