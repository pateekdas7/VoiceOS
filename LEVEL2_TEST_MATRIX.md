# VoiceOS Level-2 Test Matrix

Baseline HEAD: c33571a10b8882b1f3da5b0e0d25ebae7ce3c417
Baseline timestamp UTC: 2026-09-25T07:27:25Z

Only actually executed commands may be marked PASS or FAIL. Documentation/source claims are not execution evidence.

| Test | Category | Command/action | Expected | Actual baseline result | Status |
|---|---|---|---|---|---|
| Python suite | unit/integration/regression | pytest | Pass; coverage gate >=85% | Could not execute because repo clone was blocked by DNS/network in coding container | NOT EXECUTED |
| Ruff lint | static | ruff check src/ tests/ | Exit 0 | Not executed | NOT EXECUTED |
| Ruff format | static | ruff format --check src/ tests/ | Exit 0 | Not executed | NOT EXECUTED |
| Mypy | static/type | mypy --strict src/ tests/ | Exit 0 | Not executed | NOT EXECUTED |
| Jest | unit/integration/regression | npm test -- --runInBand | Pass | Not executed | NOT EXECUTED |
| Frontend build | build | npm run build | Success | Not executed | NOT EXECUTED |
| GitHub CI | CI | workflow run for baseline HEAD | Green | No workflow runs returned | NOT EXECUTED |
| Vercel | deployment | GitHub commit status | Success | failure | FAIL |

Future rows must include: test name, category, exact command, environment, expected result, actual result, pass/fail, timestamp, commit SHA and failure details.

Required categories across Level-2: unit, integration, API, contract, regression, failure/edge-case, security, performance, load, chaos, E2E and runtime.

## Workstream 1 execution matrix

| Test | Category | Exact command/action | Expected | Actual | Status |
|---|---|---|---|---|---|
| BFF Jest suite | regression | `npm test -- --runInBand` | All tests pass | Not executed locally; CI enabled on branch | NOT EXECUTED |
| Health route tests | unit | `pytest tests/unit/services/test_web_api_health_routes.py -q` | Pass | Not executed locally | NOT EXECUTED |
| systemd policy tests | unit/config | `pytest tests/unit/deployment/test_phase7_systemd_units.py -q` | Pass | Not executed locally | NOT EXECUTED |
| Ruff | static | `ruff check src/ tests/` | Exit 0 | Not executed locally | NOT EXECUTED |
| Ruff format | static | `ruff format --check src/ tests/` | Exit 0 | Not executed locally | NOT EXECUTED |
| Mypy | static/type | `mypy --strict src/ tests/` | Exit 0 | Not executed locally | NOT EXECUTED |
| Compose config | config | `docker compose config --quiet` | Exit 0 | Not executed locally | NOT EXECUTED |
| Backup verification | runtime | `sudo /bin/bash /opt/voiceos/scripts/backup/verify_backup_artifacts.sh` | Exit 0 | CPU node unavailable | RUNTIME EVIDENCE REQUIRED |
| Restart/readiness drill | runtime | `systemctl restart voiceos-bff` + live/ready probes | Ready healthy | CPU node unavailable | RUNTIME EVIDENCE REQUIRED |
| Dependency drill | runtime | Stop Redis/Postgres safely, probe ready, restore | Ready 503 then 200 | CPU node unavailable | RUNTIME EVIDENCE REQUIRED |
| Alert drill | runtime | Fire controlled Prometheus alert; inspect Alertmanager | Fire/route/resolve | Monitoring environment unavailable | RUNTIME EVIDENCE REQUIRED |
| Restore drill | runtime | Existing Postgres/Mongo/Vault non-destructive DR drills | Restore + integrity pass | DR environment unavailable | RUNTIME EVIDENCE REQUIRED |


## 2026-09-25 execution attempt — 2026-09-25T07:51:30Z UTC
| Test | Category | Exact command/action | Expected | Actual | Status |
|---|---|---|---|---|---|
| Repository execution-environment probe | environment | `git clone --branch claude/ssh-gpu-cpu-servers-y99fib --depth 1 https://github.com/pateekdas7/VoiceOS.git /tmp/VoiceOS` | Repository checkout succeeds | Exit 128: `Could not resolve host: github.com` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Local git working-tree status | environment | `git status --short` | Run inside repository checkout | Exit 128: `fatal: not a git repository` | NOT EXECUTED — ENVIRONMENT BLOCKED |

Because the repository checkout failed, no pytest, Jest, Ruff, Mypy, frontend build, Compose, integration, or runtime command was executed in this environment. No test is marked PASS on source inspection alone.


## MongoDB monitoring remediation tests
| Test | Category | Exact command | Expected | Actual | Status |
|---|---|---|---|---|---|
| MongoDB monitoring configuration tests | unit/config | `pytest tests/unit/monitoring/test_mongodb_monitoring.py -q` | Configuration assertions pass | Repository checkout unavailable; command could not execute | NOT EXECUTED — ENVIRONMENT BLOCKED |
| MongoDB runtime monitoring | runtime | Prometheus scrape + controlled MongoDB failure/recovery | `up` and `mongodb_up` reflect state and recover | No authorized runtime environment attached | RUNTIME EVIDENCE REQUIRED |
| MongoDB Alertmanager drill | runtime/alerting | Controlled alert fire → route → resolve | Alert fires, routes and resolves | No authorized Alertmanager environment attached | RUNTIME EVIDENCE REQUIRED |


## Jaeger/OTel retention enforcement tests
| Test | Category | Exact command | Expected | Actual | Status |
|---|---|---|---|---|---|
| Jaeger retention configuration | unit/config | `pytest tests/unit/monitoring/test_jaeger_retention.py -q` | 7-day Badger TTL is present on the actual Jaeger Deployment and storage wiring is consistent | Repository checkout unavailable in execution environment | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Jaeger runtime retention | runtime | Deploy Jaeger, write timestamped traces, verify traces older than 7 days are no longer queryable/storage-retained while current traces remain queryable | 7-day retention enforced by deployed instance | No authorized Jaeger runtime environment attached | RUNTIME EVIDENCE REQUIRED |


## Workstream 2 tests
| Test | Category | Command | Status |
|---|---|---|---|
| Tenant phone resolver | unit | `pytest tests/unit/services/test_telephony_phone_numbers.py -q` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Twilio tenant routing | integration | `pytest tests/integration/services/test_twilio_tenant_routing.py -q` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Twilio admission regression | regression | `pytest tests/unit/services/test_twilio_admission.py -q` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Twilio WSS regression | integration | `pytest tests/integration/services/test_twilio_ws_entrypoint_integration.py -q` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Dialer regression | unit | `pytest tests/unit/services/test_dialer.py -q` | NOT EXECUTED — ENVIRONMENT BLOCKED |
| BFF callback regression | unit/integration | `npm test -- --runInBand tests/jest/bff/dialer.test.js` | NOT EXECUTED — ENVIRONMENT BLOCKED |

## Workstream 2 — latest tests
- `tests/jest/bff/telephony_call_state.test.js` — state normalization, forward transitions, backward-event rejection, terminal idempotency — **NOT EXECUTED — ENVIRONMENT BLOCKED**.
- Existing `tests/jest/bff/dialer.test.js` — webhook signature/idempotency regression — **NOT EXECUTED — ENVIRONMENT BLOCKED**.
- Required DB migration validation for 038 — **NOT EXECUTED — NO AUTHORIZED POSTGRES RUNTIME**.
- Provider CPS integration test — **NOT YET ADDED**; requires Redis-backed worker test harness.


## W2 continuation test matrix

| Test area | Added/updated tests | Execution |
|---|---|---|
| Recording lifecycle | tests/unit/services/test_recording_lifecycle.py | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Callback timezone/DST | tests/jest/bff/telephony_callback_policy.test.js | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Provider failure classification | tests/jest/bff/telephony_provider_failure.test.js | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Canonical call event | tests/jest/bff/telephony_call_event.test.js | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Existing state/provider boundary | tests/jest/bff/telephony_call_state.test.js, telephony_provider_boundary.test.js | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Webhook/API security | tests/jest/bff/dialer.test.js plus new contract coverage | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Migration validation | SQL/Alembic migrations 039–041 | NOT EXECUTED — ENVIRONMENT BLOCKED |
| Lint/type checks | repository-configured Python/Node checks | NOT EXECUTED — ENVIRONMENT BLOCKED |

Required regression commands remain:
pytest tests/unit/services/test_recording_lifecycle.py -q
pytest tests/unit/services/test_telephony_phone_numbers.py -q
npm test -- --runInBand tests/jest/bff/telephony_call_state.test.js tests/jest/bff/telephony_provider_boundary.test.js tests/jest/bff/telephony_callback_policy.test.js tests/jest/bff/telephony_provider_failure.test.js tests/jest/bff/telephony_call_event.test.js tests/jest/bff/dialer.test.js
plus repository lint/type/migration validation where configured.


### W2 continuation test evidence — 2026-09-25
Added focused tests for recording lifecycle, callback timezone/DST, provider failure classification, canonical telephony events, and bounded telephony metrics. **NOT EXECUTED — ENVIRONMENT BLOCKED**: no repository checkout/executable test runtime is available. GitHub Actions returned no workflow runs for current W2 HEAD `6963d0411cd4569cfa1e7087d554ac6af14e59f3`.


## W2 definitive acceptance-to-test inventory — 2026-09-25

Only execution can produce PASS/FAIL. All rows below remain NOT EXECUTED or RUNTIME EVIDENCE REQUIRED until the command/action is actually run.

| W2 requirement | Implementation location | Test location / action | Exact command/action | Execution status | Evidence | Remaining blocker |
|---|---|---|---|---|---|---|
| Tenant-owned phone registration/resolution | `src/services/telephony/phone_numbers.py`, migration 037 | `tests/unit/services/test_telephony_phone_numbers.py` | `pytest tests/unit/services/test_telephony_phone_numbers.py -q` | NOT EXECUTED | Source/test files only | Executable repo runtime unavailable |
| Tenant-aware inbound /voice routing | `src/services/media_gateway/twilio_ws_entrypoint.py` | `tests/integration/services/test_twilio_tenant_routing.py` | `pytest tests/integration/services/test_twilio_tenant_routing.py -q` | NOT EXECUTED | Source/test files only | Executable repo runtime unavailable |
| Signed Twilio admission | `src/services/media_gateway/twilio_ws_entrypoint.py` | `tests/unit/services/test_twilio_admission.py` | `pytest tests/unit/services/test_twilio_admission.py -q` | NOT EXECUTED | Source/test files only | Executable repo runtime unavailable |
| Media Streams WSS lifecycle | `src/services/media_gateway/twilio_ws_entrypoint.py`, CPU composition root | `tests/integration/services/test_twilio_ws_entrypoint_integration.py` | `pytest tests/integration/services/test_twilio_ws_entrypoint_integration.py -q` | NOT EXECUTED | Source/test files only | No runtime / no Twilio |
| Outbound dialer + caller ID | `dialer_worker.js` | `tests/unit/services/test_dialer.py`, dialer Jest tests | `pytest tests/unit/services/test_dialer.py -q` and targeted Jest | NOT EXECUTED | Source/test files only | No executable runtime |
| Canonical call state transitions | `telephony_call_state.js` | `tests/jest/bff/telephony_call_state.test.js` | `npm test -- --runInBand tests/jest/bff/telephony_call_state.test.js` | NOT EXECUTED | Source/test files only | No Node checkout/runtime |
| Webhook signature/auth boundary | `bff.js` | `tests/jest/bff/dialer.test.js`, provider boundary tests | `npm test -- --runInBand tests/jest/bff/dialer.test.js tests/jest/bff/telephony_provider_boundary.test.js` | NOT EXECUTED | Source/test files only | No executable runtime |
| CallSID correlation / tenant mismatch | `bff.js` | targeted callback/security tests | `npm test -- --runInBand tests/jest/bff/dialer.test.js` plus DB-backed integration when available | NOT EXECUTED | Source/test files only | DB/test runtime unavailable |
| Webhook idempotency / duplicate suppression | `bff.js`, `idempotency_keys` | callback regression tests + DB integration | targeted Jest + PostgreSQL integration | NOT EXECUTED | Source/test files only | PostgreSQL unavailable |
| Provider failure classification | `telephony_provider_failure.js` | `tests/jest/bff/telephony_provider_failure.test.js` | `npm test -- --runInBand tests/jest/bff/telephony_provider_failure.test.js` | NOT EXECUTED | Source/test files only | No Node runtime |
| Callback timezone / DST | `telephony_callback_policy.js` | `tests/jest/bff/telephony_callback_policy.test.js` | `npm test -- --runInBand tests/jest/bff/telephony_callback_policy.test.js` | NOT EXECUTED | Source/test files only | No Node runtime |
| Tenant CPS limiting | `dialer_worker.js` | Redis-backed worker test still required | Start Redis and execute CPS/concurrency test | NOT EXECUTED | Source implementation only | Redis runtime/test harness unavailable |
| Recording lifecycle | `src/services/media_gateway/recording_lifecycle.py`, migration 039 | `tests/unit/services/test_recording_lifecycle.py` | `pytest tests/unit/services/test_recording_lifecycle.py -q` | NOT EXECUTED | Source/test files only | No Python runtime |
| Recording tenant/access security | BFF recording route + media gateway access boundary | targeted API/security integration | Valid/invalid tenant, signature, expiry and path traversal cases against running services | RUNTIME EVIDENCE REQUIRED | Source controls exist | No runtime |
| Telephony metrics | `src/services/media_gateway/metrics.py`, BFF metrics | `tests/unit/services/test_telephony_metrics.py` | `pytest tests/unit/services/test_telephony_metrics.py -q` | NOT EXECUTED | Source/test files only | No Python runtime |
| Canonical event construction | `telephony_call_event.js` | `tests/jest/bff/telephony_call_event.test.js` | `npm test -- --runInBand tests/jest/bff/telephony_call_event.test.js` | NOT EXECUTED | Source/test files only | No Node runtime |
| Canonical event schema/serialization | `telephony_event_boundary.js` | `tests/jest/bff/telephony_event_boundary.test.js` | `npm test -- --runInBand tests/jest/bff/telephony_event_boundary.test.js` | NOT EXECUTED | Source/test files only | No Node runtime |
| Event persistence + outbox | migration 042, `telephony_event_boundary.js` | DB-backed integration test required | Apply migration 042 and exercise transaction + duplicate event | NOT EXECUTED | Source implementation only | No authorized PostgreSQL runtime |
| Event retry/backoff | `scripts/telephony/telephony_event_relay.js` | `tests/jest/bff/telephony_event_relay.test.js` + Redis integration | `npm test -- --runInBand tests/jest/bff/telephony_event_relay.test.js` | NOT EXECUTED | Source/test files only | No Node/Redis runtime |
| Event DLQ | migration 042 + relay | relay failure injection | Exhaust attempts and inspect `telephony_event_dlq` | RUNTIME EVIDENCE REQUIRED | Source contract exists | No PostgreSQL/Redis runtime |
| Downstream unavailable | relay | Redis outage integration test | Stop Redis during relay and verify bounded retry/DLQ behavior | RUNTIME EVIDENCE REQUIRED | Source failure path exists | No Redis runtime |
| Migration ordering/safety | migrations 037–042 | `tests/unit/services/test_telephony_migrations.py` | `pytest tests/unit/services/test_telephony_migrations.py -q` | NOT EXECUTED | Static migration inventory only | No executable repo runtime |
| Real Twilio outbound | Twilio provider + dialer | real provider call | Authorized Twilio credentials/number; execute outbound call | RUNTIME EVIDENCE REQUIRED | None | External Twilio infrastructure unavailable |
| Real inbound | /voice + phone resolver | real provider call | Authorized Twilio number; execute inbound call | RUNTIME EVIDENCE REQUIRED | None | External Twilio infrastructure unavailable |
| Real Media Streams | WSS + CallOrchestrator | real provider call | Verify stream start/audio/STT/TTS/disconnect | RUNTIME EVIDENCE REQUIRED | None | External runtime unavailable |
| Prometheus scrape | metrics endpoints / Prometheus config | live scrape | Query telephony metrics from Prometheus | RUNTIME EVIDENCE REQUIRED | None | Monitoring runtime unavailable |

### W2 full regression commands
- `pytest -q`
- `ruff check src/ tests/`
- `ruff format --check src/ tests/`
- `mypy --strict src/ tests/` if configured
- `npm test -- --runInBand`
- `docker compose config --quiet`
- migration application/rollback validation for 037–042 against authorized PostgreSQL
- Redis integration/chaos checks for CPS, callback idempotency, and event relay
- authorized Twilio runtime checks where credentials and numbers exist

No command above is marked PASS without execution evidence.
