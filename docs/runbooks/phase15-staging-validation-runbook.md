# Phase 15 — Staging Validation Runbook

**Execute after Phase 14 source verification passes (all 10 chaos scenarios clean).**

---

## Prerequisites

1. All Phase 1–14 services committed and running on the production VM
2. Source test: `node tests/unit/bff/test_phase15_source.js` → 22/22
3. Twilio test credentials: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (from Twilio console)
4. Tools available: `gitleaks`, `docker` (for OWASP ZAP), optional `trufflehog`

---

## 15a — Staging Environment Setup

```bash
# On the production VM (205.147.102.94):
bash scripts/staging/setup_staging.sh
```

This creates:
- PostgreSQL database `voiceos_staging` with Alembic migrations applied
- Staging Redis on port 6479
- Staging bff.js on port 8100
- Staging web_api on port 8101
- Env file at `deployment/cpu/.env.staging`

Fill in `deployment/cpu/.env.staging` with real Twilio test credentials before running scenarios.

**Verify staging is up:**
```bash
curl http://localhost:8100/health
curl http://localhost:8101/health
redis-cli -p 6479 ping
psql voiceos_staging -c "SELECT COUNT(*) FROM tenants;"
```

---

## 15b — End-to-End Scenarios

```bash
python tests/e2e/test_phase15_staging.py \
  --bff-url http://localhost:8100 \
  --pg-dsn postgresql://localhost/voiceos_staging
```

Or run individually:
```bash
python tests/e2e/test_phase15_staging.py --bff-url http://localhost:8100 --scenario 1
```

### Scenario 1: Full happy path
Creates tenant → login → campaign → upload 5 leads → polls import to DONE.

**Expected:** All steps return 2xx, `import_id` status reaches `DONE` within 30s.

### Scenario 2: Post-call write durability
Seeds a `call_attempts` row with `IN_PROGRESS` status, verifies DB connectivity.

**Expected:** Row seeded; full verification requires dialer_worker running with the callback flow.

### Scenario 3: HITL escalation
POSTs to `/hitl/queue/claim-next`. Expects 404 (empty queue) or 200 with an item.

**Expected:** No crash, 401 not returned (auth must be valid).

### Scenario 4: Import resume
Uploads 100 rows, polls to DONE, verifies ≥ 90 valid rows processed.

**Expected:** `status=DONE`, `valid_rows >= 90`.

### Scenario 5: Crash recovery
Sends SIGKILL to `dialer_worker`, restarts it, checks `recovery_log`.

**Expected:** `recovery_log` receives entries within 10s of restart.

### Scenario 6: Billing — invoice generation
GETs `/billing/invoices`. Accepts 200 with an invoice list or 404 (Python web_api billing).

**Expected:** No 5xx, route accessible.

---

## 15c — Load Test

```bash
BFF_URL=http://localhost:8100 \
STAGING_TOKEN=<valid-jwt-from-login> \
locust -f tests/load/locustfile_bff.py --headless \
  --users 50 --spawn-rate 5 --run-time 10m \
  --host http://localhost:8100
```

**Gates:**
- Error rate < 1% (zero 5xx required)
- p95 response time < 2000ms
- No queue depth > 50 (monitor Redis: `redis-cli -p 6479 LLEN pending_calls:*`)

**Reporting:** Locust prints a summary table. The `assert_gates` hook exits with code 1 on failure.

---

## 15d — Security Scan

```bash
STAGING_URL=http://localhost:8100 bash tests/security/phase15_security_scan.sh
```

Reports written to `reports/security/phase15/`.

**Manual ZAP scan (if docker unavailable):**
```bash
docker run --rm -v $(pwd)/reports:/zap/wrk/:rw \
  ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
  -t http://localhost:8100 -r zap-report.html
```

**Pass criteria:**
- gitleaks: 0 secrets found
- OWASP ZAP: 0 HIGH alerts
- trufflehog: 0 high-confidence findings
- No PII in logs (`python scripts/check_pii_logs.py` exits 0)

---

## 15e — Teardown

```bash
bash scripts/staging/teardown_staging.sh

# To also drop the database:
bash scripts/staging/teardown_staging.sh --drop-db
```

---

## Completion Criteria

- [ ] All 6 staging scenarios pass (or skip with documented reason)
- [ ] Load test: 0 5xx, p95 < 2000ms, queue depth < 50
- [ ] Security scan: gitleaks clean, ZAP 0 HIGH alerts

All gates met → proceed to Phase 16 (Production Rollout).
