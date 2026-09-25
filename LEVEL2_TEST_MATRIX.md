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