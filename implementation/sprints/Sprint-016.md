# Sprint-016 — Concurrency, Circuit Breakers, Health & Observability

**Epic:** E4 — Reliability Infrastructure  
**Status:** ⬜ Pending  
**Depends on:** Sprint-013, Sprint-014, Sprint-015  
**Blocks:** Sprint-026  
**Milestone:** Reliability Complete — M-4  

---

## Objective

Complete the reliability layer: single-writer concurrency architecture, bounded queues with backpressure, circuit breakers, health monitoring, service discovery, and the full observability stack (Prometheus metrics, structured JSON logs, distributed tracing). After this sprint, the system is durable, observable, and self-protecting.

---

## Architecture References

- Volume 3: Ch9 (Concurrency Architecture — per-call thread, single-writer, fair-share, backpressure, load shedding), Ch10 (Queue Management — bounded queues, priority, retry, DLQ), Ch11 (Service Discovery), Ch12 (Health Monitoring), Ch13 (Failover — circuit-level), Ch14 (Circuit Breakers), Ch15 (Observability — metrics), Ch16 (Logging), Ch17 (Tracing)
- Volume 7: Ch7 (Monitoring Platform — Prometheus/Grafana, SLO attainment)
- DocSuite-08: Testing Catalog

---

## Components to Implement

### `src/libs/concurrency/`
- `worker_pool.py` — WorkerPool: async task pool with fair-share scheduling, configurable max workers
- `bounded_queue.py` — BoundedQueue: asyncio.Queue with max_size enforced; `put_nowait()` raises `QueueFullError` on overflow (RI-3)
- `load_shedder.py` — LoadShedder: when system load > threshold, drops lowest-priority work first
- `backpressure.py` — BackpressureSignal: emitted when downstream queue is > 80% full; upstream producers slow down

### `src/libs/circuit-breaker/`
- `breaker.py` — CircuitBreaker: CLOSED → OPEN → HALF_OPEN state machine
- States: CLOSED (normal), OPEN (fail fast, no calls), HALF_OPEN (test call)
- Thresholds: configurable per service (default: open after 5 failures in 30s)
- `call(fn, *args) -> Result | CircuitOpenError` — wraps any external call
- Per-service breakers: each external dependency (STT, LLM, TTS, Postgres, Redis, MongoDB) has its own breaker

### `src/libs/health/`
- `protocol.py` — HealthCheck protocol: `async def check() -> HealthStatus`
- `probe.py` — ReadinessProbe (is service ready to receive traffic?), LivenessProbe (is service alive?)
- `aggregator.py` — HealthAggregator: collects all component health checks, reports overall status
- HTTP endpoint: `GET /health/live` (liveness), `GET /health/ready` (readiness)

### `src/libs/service-discovery/`
- `registry.py` — ServiceRegistry: registers service endpoints on startup
- `resolver.py` — ServiceResolver: looks up endpoint by service name (Kubernetes DNS-based)
- `client.py` — ServiceClient: combines resolver + circuit breaker + retry for inter-service calls

### `src/libs/observability/`
- `metrics.py` — PrometheusClient: standard RED metrics (Requests, Errors, Duration) for every service; call_count, intent_distribution, negotiation_outcome counters
- `logger.py` — StructuredLogger: JSON structured logging with required fields: `timestamp`, `level`, `service`, `tenant_id`, `call_id`, `trace_id`, `correlation_id`, `message`
- `tracer.py` — OTelTracer: OpenTelemetry tracer; `start_span(name, attributes)` → context manager; trace propagation via W3C Trace Context headers
- `grafana/` — Dashboard JSON files: SLO attainment, error-budget burn, GPU utilization, per-service latency histograms, call funnel

---

## Files Expected to Change

**New:** `src/libs/concurrency/`, `src/libs/circuit-breaker/`, `src/libs/health/`, `src/libs/service-discovery/`, `src/libs/observability/`  
**New:** `monitoring/grafana/dashboards/` (dashboard JSON files)  
**New:** `tests/unit/libs/test_circuit_breaker.py`, `test_bounded_queue.py`, `test_observability.py`  
**New:** `tests/integration/libs/test_circuit_breaker_integration.py`

---

## Acceptance Criteria

- [ ] BoundedQueue raises `QueueFullError` when max_size exceeded — never silently drops or blocks indefinitely (RI-3)
- [ ] CircuitBreaker transitions CLOSED → OPEN after 5 failures; stays OPEN; transitions OPEN → HALF_OPEN after cooldown; HALF_OPEN → CLOSED on success
- [ ] CircuitBreaker in OPEN state returns `CircuitOpenError` immediately (no waiting)
- [ ] StructuredLogger emits valid JSON with all required fields on every log line
- [ ] OTelTracer propagates trace_id across service boundaries (verified in integration test)
- [ ] `GET /health/live` returns 200 when service is healthy, 503 when unhealthy
- [ ] `GET /health/ready` returns 200 only when all dependencies are healthy
- [ ] Prometheus metrics are scraped successfully from `/metrics` endpoint

---

## Required Tests

**Unit:**
- `test_bounded_queue_overflow_raises` — put max_size+1 items → QueueFullError
- `test_circuit_breaker_open_after_threshold` — 5 failures → state=OPEN
- `test_circuit_breaker_half_open_success` — after cooldown, success → state=CLOSED
- `test_circuit_breaker_open_returns_immediately` — circuit OPEN → CircuitOpenError without calling fn
- `test_structured_logger_json_fields` — log message → JSON with all required fields
- `test_otel_tracer_context_propagation` — parent span → child span shares trace_id

**Integration:**
- `test_otel_trace_end_to_end` — fake call path with 3 services → single trace with 3 spans visible in collector

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] RI-3 invariant enforced: BoundedQueue has max_size (100% coverage of queue instantiation)
- [ ] CI green
- [ ] Grafana dashboard JSON files committed
- [ ] **Milestone M-4 (Reliability Complete) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-017

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Circuit breaker and observability tests are fully in-process. Health endpoint tests use `TestClient` (ASGI test client).

### Files Created

- `src/libs/concurrency/__init__.py`, `worker_pool.py`, `bounded_queue.py`, `load_shedder.py`, `backpressure.py`
- `src/libs/circuit-breaker/__init__.py`, `breaker.py`
- `src/libs/health/__init__.py`, `protocol.py`, `probe.py`, `aggregator.py`
- `src/libs/service-discovery/__init__.py`, `registry.py`, `resolver.py`, `client.py`
- `src/libs/observability/__init__.py`, `metrics.py`, `logger.py`, `tracer.py`
- `monitoring/grafana/dashboards/` (5 Grafana dashboard JSON files)
- `tests/unit/libs/test_circuit_breaker.py`, `test_bounded_queue.py`, `test_observability.py`
- `tests/integration/libs/test_circuit_breaker_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| External service (circuit breaker) | `AsyncMock` with configurable fail rate | Simulates STT/LLM/TTS/DB failure |
| OTel collector | In-memory span exporter | Captures spans without real Jaeger |
| Prometheus | `prometheus_client` test registry | In-process metrics, no Prometheus server |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations; BoundedQueue always has max_size |
| Unit tests | `pytest tests/unit/libs/` | All pass |
| Integration tests | `pytest tests/integration/libs/test_circuit_breaker_integration.py` | Passes |
| Grafana validation | `grafana-dashboard-linter` on all JSON files | 0 errors |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `BoundedQueue`: put max_size+1 → `QueueFullError` (RI-3)
- `CircuitBreaker`: 5 failures → OPEN; cooldown → HALF_OPEN; success → CLOSED
- `CircuitBreaker` OPEN → `CircuitOpenError` immediately (no waiting)
- `StructuredLogger`: every log line is valid JSON with all required fields
- `OTelTracer`: parent span → child span shares `trace_id`

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services integrated/upgraded this sprint:**

| Action | Service | Why |
|---|---|---|
| Add CircuitBreaker wrappers | All services calling STT, LLM, TTS, Postgres, Redis, MongoDB | Self-protecting failure isolation |
| Add BoundedQueue to hot-path queues | ConversationEngine, PlaybackScheduler | RI-3 enforcement on deployed infrastructure |
| Wire StructuredLogger | All services (rolling restarts) | JSON logs visible in Loki from next sprint |
| Wire OTelTracer | All services (rolling restarts) | Distributed traces to OTel collector |
| Add `/health/live` and `/health/ready` | All services | Kubernetes liveness/readiness probes |
| Deploy ServiceRegistry | `voiceos-runtime` namespace | Endpoint registration for all services |
| Deploy Grafana dashboards | Grafana instance (from `docker-compose.yml`) | SLO, call funnel, GPU fleet, reliability dashboards |

**Previously deployed services that remain running:**
- All Sprint-004–015 services

**Deployment procedure:**
1. Rolling restart all services with CircuitBreaker + StructuredLogger + OTelTracer wiring
2. Deploy Grafana dashboard JSON files to Grafana provisioning directory
3. Configure Kubernetes liveness/readiness probes for all Deployments
4. Verify BoundedQueue max_size enforced: send burst of 200 requests to ConversationEngine → QueueFullError at max_size

**Health checks:**
- All services: `GET /health/live` → 200; `GET /health/ready` → 200
- Prometheus: all `/metrics` endpoints reachable, RED metrics visible
- Grafana: SLO dashboard loads, call funnel panel renders
- Circuit breakers: `circuit_breaker_state{service="llm"}` metric = CLOSED at steady state

**Integration validation:**
- Simulate LLM failure (kill vLLM pod): ConversationEngine circuit breaker opens after 5 failures; `CircuitOpenError` returned instead of hanging
- OTel trace: test call produces trace in OTel in-memory exporter; 3+ spans visible (GW, CIL, LLM)
- Structured log: grep ConversationEngine logs → every line is valid JSON with `tenant_id`, `call_id`, `trace_id`

**Rollback procedure:**
- Rolling restart is additive (libraries wired in); rollback = previous image without new wiring
- Grafana dashboards: remove from provisioning directory; Grafana auto-removes on restart

### GPU Node

> **GPU node is not required during this sprint.** GPU circuit breakers are wired (via GPU Scheduler service integration) but GPU services themselves unchanged. Previously deployed GPU services (Whisper, Qwen, Veena) remain running unchanged.

### Infrastructure Validation

**CPU Validation:**
- BoundedQueue: Prometheus `queue_size_max` gauge is set for all hot-path queues (RI-3 compliance confirmed)
- CircuitBreaker: LLM circuit breaker transitions CLOSED → OPEN → HALF_OPEN → CLOSED (simulated failure + recovery in staging)
- Health endpoints: Kubernetes liveness/readiness probes fire correctly (restart unhealthy pods)
- Structured logs: every log line contains `tenant_id`, `service`, `call_id`, `trace_id`

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- OTel trace propagation: W3C `traceparent` header forwarded across service boundaries
- End-to-end trace visible in OTel exporter for a test call

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes after circuit breaker wiring
- IdempotencyGuard: `pytest tests/integration/libs/test_recovery_integration.py` — still passes
- EventBus: events still flowing; DLQ depth = 0
- All GPU service health endpoints: 200

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] CircuitBreaker (CLOSED/OPEN/HALF_OPEN) implemented
- [ ] BoundedQueue with max_size enforcement (RI-3)
- [ ] StructuredLogger (valid JSON all fields), OTelTracer implemented
- [ ] Health probes (live/ready), ServiceRegistry implemented
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Grafana dashboard JSON files validate
- [ ] All unit + integration tests pass
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All services have liveness/readiness probes active in Kubernetes
- [ ] CircuitBreaker verified OPEN on simulated dependency failure
- [ ] BoundedQueue max_size enforced on all hot-path queues (Prometheus confirms)
- [ ] Structured JSON logs visible for all services
- [ ] OTel trace spans captured for end-to-end test call
- [ ] Grafana dashboards rendering in Grafana UI
- [ ] All regression tests pass
- [ ] Milestone M-4 (Reliability Complete) criteria verified
- [ ] Deployment remains active as baseline for Sprint-017

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state.

### CPU_NODE_STATE.md — Updates This Sprint

- Update ALL services in §8.1 to note they now include: `CircuitBreaker`, `BoundedQueue`, `StructuredLogger`, `OTelTracer` (rolling restart applied this sprint)
- Add `OTelTracer` environment variables: `OTEL_EXPORTER_OTLP_ENDPOINT`, `SERVICE_NAME` (§11)
- Update §13 Observability: mark Prometheus and Grafana as "pre-configured basic instance" (Sprint-027 deploys full stack)
- Add note in §8.3 Startup Order: all services require `OTEL_EXPORTER_OTLP_ENDPOINT` set before start
- Add Grafana dashboard URLs to §14 Health Checks section

### GPU_NODE_STATE.md — Updates This Sprint

GPU node unchanged. `GPU_NODE_STATE.md` last_updated remains: Sprint-012.

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/.env.example` | Add `OTEL_EXPORTER_OTLP_ENDPOINT`, `SERVICE_NAME` variable descriptions |
| `deployment/cpu/healthcheck.sh` | Add CircuitBreaker state check: all circuit breakers CLOSED at startup |

### DR Validation

**CPU node rebuild test:**
```bash
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all services with CircuitBreaker wired; BoundedQueue max_size gauge exposed
```

**OTel trace validation:**
```bash
# Inject one test call; verify trace in Jaeger/Tempo
python3 scripts/validate/trace_roundtrip.py
# Expected: complete trace with spans from MediaGateway through AudioOutput
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; Milestone M-4 criteria verified after rebuild
```
