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
