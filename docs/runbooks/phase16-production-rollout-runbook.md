# Phase 16 — Controlled Production Rollout Runbook

**Prerequisites:** Phase 15 staging validation passed (all 6 scenarios, load test, security scan).

---

## 16a — Pre-Deploy Checklist

Run and verify 0 failures before touching production:

```bash
bash scripts/deploy/pre_deploy_checklist.sh
```

Manual verification checklist:
- [ ] Phase 13/14/15 source tests: all green
- [ ] All 5 systemd units exist and have `TimeoutStopSec`
- [ ] `check_secrets.py` and `check_pii_logs.py` exit 0
- [ ] Vault backup current: `systemctl status voiceos-vault-snapshot.timer`
- [ ] PostgreSQL WAL archiving active: `psql -c "SHOW archive_mode;"`
- [ ] Alertmanager: `curl http://localhost:9093/api/v2/status | jq .status`
- [ ] All runbooks reviewed: `ls docs/runbooks/`
- [ ] Rollback SHA noted: `git rev-parse HEAD`

---

## 16b — Deployment Order

```bash
# Dry run first (no changes):
bash scripts/deploy/deploy.sh --dry-run

# Real deployment (destructive — restarts all services):
bash scripts/deploy/deploy.sh
```

Deployment steps in order:
1. `alembic upgrade head` — apply any pending migrations
2. `kubectl rollout restart` — K8s services if cluster is joined
3. Install / refresh systemd units from `scripts/systemd/`
4. `systemctl restart voiceos-webapi` + health check `:8001/health`
5. `systemctl restart voiceos-bff` + health check `:8000/health`
6. `systemctl restart voiceos-voice-runtime` + health check `:8010/health`
7. `systemctl restart voiceos-dialer-worker` (reconciliation runs before consumer loop)
8. Enable backup timers: `voiceos-mongodb-backup.timer`, `voiceos-vault-snapshot.timer`

**Watch for 10 minutes after deploy:**
```bash
journalctl -u voiceos-bff -u voiceos-webapi -u voiceos-dialer-worker -f
```

---

## 16c — Single Tenant First Rollout

```bash
bash scripts/deploy/rollout_first_tenant.sh \
  --tenant-id <uuid> \
  --dry-run  # remove --dry-run when ready
```

Process:
1. Creates a campaign in `SIMULATION` mode (dials Twilio test numbers, no real calls)
2. Health-checks bff.js and web_api
3. Activates `dialing_enabled: true, mode: simulation` for the tenant

**Verification:**
```bash
# Watch first call attempt appear:
journalctl -u voiceos-dialer-worker -f | grep call_attempt

# Verify DB records:
psql $DATABASE_URL -c "SELECT * FROM call_attempts ORDER BY created_at DESC LIMIT 5;"
psql $DATABASE_URL -c "SELECT * FROM active_calls ORDER BY created_at DESC LIMIT 5;"
```

**Once simulation passes → enable real dialing:**
```bash
psql $DATABASE_URL -c "
  UPDATE tenants
  SET metadata = jsonb_set(metadata, '{mode}', '\"production\"')
  WHERE tenant_id = '<uuid>';
"
```

**Expansion to other tenants:** enable one per 12 hours over 48 hours.

---

## 16d — Rollback Procedure

```bash
# Preview what rollback would do (safe):
bash scripts/deploy/rollback.sh --to HEAD~1

# Execute rollback (destructive):
bash scripts/deploy/rollback.sh --to <sha> --execute
```

The script:
1. Stops `voiceos-dialer-worker` (SIGTERM, graceful drain)
2. Stops `voiceos-voice-runtime` (drain gate)
3. Checks if Alembic downgrade needed (only if migration files changed)
4. Runs `alembic downgrade -1` if needed
5. `git checkout <sha>`
6. Re-installs systemd units from rollback target
7. Restarts all services
8. Health checks

**Target:** Full rollback in < 15 minutes. The script reports elapsed time.

**Emergency manual rollback (if script fails):**
```bash
# 1. Stop services
systemctl stop voiceos-dialer-worker voiceos-voice-runtime voiceos-bff voiceos-webapi

# 2. Checkout previous SHA
git checkout <previous-sha>

# 3. Downgrade if needed
alembic downgrade -1

# 4. Restart
systemctl start voiceos-webapi voiceos-bff voiceos-voice-runtime voiceos-dialer-worker
```

---

## Service Health URLs

| Service | URL |
|---------|-----|
| bff.js | `http://localhost:8000/health` |
| web_api | `http://localhost:8001/health` |
| voice runtime | `http://localhost:8010/health` |
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Grafana | `http://localhost:3000` |

---

## Post-Rollout Monitoring (first 48h)

```bash
# Error rate (should be 0):
curl -sg 'http://localhost:9090/api/v1/query?query=rate(http_requests_total{status=~"5.."}[5m])' | jq .

# Active calls:
psql $DATABASE_URL -c "SELECT COUNT(*) FROM active_calls;"

# Queue depth:
redis-cli LLEN "pending_calls:*"

# Reconciliation in last hour:
psql $DATABASE_URL -c "SELECT COUNT(*) FROM recovery_log WHERE created_at > NOW() - INTERVAL '1 hour';"
```
