# Sprint-003 — Testing Infrastructure, CI/CD Pipeline & Developer Tooling

**Epic:** E1 — Foundation & Engineering Infrastructure  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-001, Sprint-002  
**Blocks:** Sprint-004, Sprint-005, Sprint-006, Sprint-007  
**Milestone:** Foundation Complete  

---

## Objective

Deliver a complete, enforcing CI/CD pipeline — test pyramid harnesses, coverage gates, Docker Compose dev environment, and all developer scripts. This sprint closes Epic E1 and delivers the Foundation Complete milestone.

---

## Architecture References

- Volume 6: Ch9 (Testing Standards — pyramid + AI eval layer, coverage gates), Ch11 (Git Workflow), Ch12 (CI/CD Standards CICD-1–CICD-8)
- Volume 7: Ch4 (Deployment Strategy — canary/progressive rollout patterns)
- DocSuite-08: Testing Catalog
- DocSuite-09: Deployment Cookbook
- DocSuite-12: Engineering Templates

---

## Components to Implement

### 1. Test Harnesses (`tests/`)

**Test pyramid structure:**
```
tests/
├── conftest.py              (shared pytest fixtures: fake audio, tenant context, db session)
├── unit/                    (fast, no I/O, mock all external deps)
├── integration/             (real Postgres, real Redis, real MongoDB via Docker)
│   ├── conftest.py          (DB fixtures: create/teardown test schema)
│   └── test_db_connectivity.py
├── e2e/                     (full call path tests, real models or stubs)
│   └── conftest.py
├── load/                    (Locust or k6 load test scripts)
│   └── README.md
├── invariants/              (contract-enforcement tests, must be 100% coverage)
│   └── test_invariant_suite.py  (import + callable check for all 8 guards)
└── ai_eval/                 (AI evaluation: intent accuracy, hallucination checks)
    └── README.md
```

**Audio injection harness:**
- `tests/fixtures/audio.py` — `FakeRTPStream`: generates μ-law audio frames at configurable RTP sequence, simulates speech/silence patterns for VAD testing
- `tests/fixtures/audio_clips/` — small WAV fixtures (silence, speech, barge-in patterns)

**Database fixtures:**
- `tests/fixtures/db.py` — `TestPostgres`: spins up Postgres via Docker, runs migrations, tears down after test session
- `tests/fixtures/redis.py` — `TestRedis`: spins up Redis via Docker

### 2. CI/CD Pipeline (`.github/workflows/`)

**`ci.yml` — Full CI pipeline (all PRs and pushes to main):**
```yaml
stages:
  1. checkout + install deps
  2. ruff check (lint + import sort)
  3. mypy --strict (type check all src/)
  4. pytest tests/unit/ --cov=src/libs/ --cov-fail-under=90
  5. pytest tests/invariants/ --cov=src/libs/invariants/ --cov-fail-under=100
  6. pytest tests/integration/ (requires Docker services)
  7. trufflehog / gitleaks (secrets scan — fail on any found secret)
  8. docker build (verify image builds cleanly)
```

**`release.yml` — Release pipeline (tags only):**
```yaml
stages:
  1. full CI
  2. docker build + push to registry
  3. helm lint
  4. notify
```

### 3. Docker Compose Dev Environment (`docker-compose.yml`)

Services:
- `postgres` — Postgres 16, with init script running migrations
- `redis` — Redis 7, with AOF persistence
- `mongodb` — MongoDB 7, with replica set (required for transactions)
- `prometheus` — Prometheus with scrape config stub
- `grafana` — Grafana with provisioned datasources

### 4. Developer Scripts (`scripts/`)

- `scripts/setup.sh` — install Python deps, pre-commit, dev tools
- `scripts/dev-up.sh` — `docker compose up -d` + wait for health
- `scripts/dev-down.sh` — `docker compose down -v`
- `scripts/seed-db.sh` — populate Postgres with test tenant + customer fixture data
- `scripts/run-tests.sh` — run all test suites in order
- `scripts/lint.sh` — ruff + mypy
- `scripts/generate-api-spec.sh` — (stub for Sprint-025)

### 5. Module Boundary Enforcement

`scripts/check_boundaries.py` — scans `src/` for cross-package imports that violate V6 Ch2 rules:
- `src/services/` must not import from `src/engines/` directly (must go through contracts)
- `src/engines/` must not import from `src/services/`
- `src/libs/contracts/` must not import from `src/libs/invariants/` or vice versa
- CI step: `python scripts/check_boundaries.py` (fails CI if violation found)

---

## Files Expected to Change

**New:** `docker-compose.yml`, `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `scripts/` (all scripts), `tests/conftest.py`, `tests/fixtures/` (audio, db, redis), `tests/integration/conftest.py`, `tests/ai_eval/README.md`, `tests/load/README.md`

**Modified:** `pyproject.toml` — add test dependencies (pytest-cov, pytest-asyncio, testcontainers or equivalent)

---

## Acceptance Criteria

- [ ] `docker compose up` starts Postgres, Redis, MongoDB cleanly (all health checks pass)
- [ ] CI pipeline runs end-to-end green on a clean checkout
- [ ] Module boundary check script catches at least one deliberate violation in a test
- [ ] `FakeRTPStream` generates valid μ-law audio frames that VAD can process (verified in unit test)
- [ ] `scripts/seed-db.sh` produces a test tenant + 3 test customers in Postgres
- [ ] Integration test `test_db_connectivity.py` connects to test Postgres, runs a query, disconnects cleanly

---

## Required Tests

- `tests/unit/test_audio_harness.py` — FakeRTPStream generates frames at correct rate, silence/speech toggles correctly
- `tests/unit/test_boundary_checker.py` — boundary checker script catches a planted violation
- `tests/integration/test_db_connectivity.py` — Postgres connectivity, migration run, basic query
- `tests/integration/test_redis_connectivity.py` — Redis ping, set/get
- `tests/integration/test_mongodb_connectivity.py` — MongoDB ping, insert/find

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] CI green including integration tests
- [ ] Docker Compose starts all services cleanly
- [ ] Module boundary check passes
- [ ] **Milestone M-1 (Foundation Complete) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-004
