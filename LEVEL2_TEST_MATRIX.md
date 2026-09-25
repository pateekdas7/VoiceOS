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

## Workstream 1 execution record — 2026-09-25

| Test name | Category | Command | Environment | Expected | Actual | Result | Commit |
|---|---|---|---|---|---|---|---|
| Backup verifier shell syntax | configuration/static | bash -n /tmp/backup_verify.sh | coding container | Exit 0 | Exit 0; syntax-ok | PASS | current Workstream-1 changes |
| Python unit/integration suite | unit/integration/regression | pytest | coding container | Pass | Repository checkout cannot be obtained because github.com DNS resolution fails | NOT EXECUTED | current Workstream-1 changes |
| Jest BFF suite | unit/integration/regression | npm test -- --runInBand | coding container | Pass | Not executable; repository checkout unavailable | NOT EXECUTED | current Workstream-1 changes |
| Ruff lint | static | ruff check src/ tests/ | coding container | Exit 0 | Not executed; repository checkout unavailable | NOT EXECUTED | current Workstream-1 changes |
| Ruff format | static | ruff format --check src/ tests/ | coding container | Exit 0 | Not executed; repository checkout unavailable | NOT EXECUTED | current Workstream-1 changes |
| Mypy | static/type | mypy --strict src/ tests/ | coding container | Exit 0 | Not executed; repository checkout unavailable | NOT EXECUTED | current Workstream-1 changes |
| Coverage | quality | pytest with project coverage gate | coding container | >=85% | Not executed | NOT EXECUTED | current Workstream-1 changes |
| Runtime service restart/recovery | runtime | systemctl stop/restart + health probes | CPU node | Correct health transitions and recovery | No authorized CPU runtime attached | RUNTIME EVIDENCE REQUIRED | current Workstream-1 changes |
| Redis/Postgres/Mongo/Vault health | integration/runtime | live health commands | CPU node | Healthy dependency probes | No live datastore environment attached | RUNTIME EVIDENCE REQUIRED | current Workstream-1 changes |
| GPU failure/recovery | runtime/chaos | deployment/gpu/healthcheck.sh + controlled service failure | GPU node | Detection and recovery | No live GPU environment attached | RUNTIME EVIDENCE REQUIRED | current Workstream-1 changes |
| Prometheus alert firing/resolution | integration/runtime | real metric injection/observation | monitoring environment | Alert fires and resolves | No monitoring runtime attached | RUNTIME EVIDENCE REQUIRED | current Workstream-1 changes |
| Backup timer/artifact verification | integration/runtime | systemctl timer + verify_backup_artifacts.sh | CPU node | Recent artifacts and zero verification failures | No CPU backup environment attached | RUNTIME EVIDENCE REQUIRED | current Workstream-1 changes |
