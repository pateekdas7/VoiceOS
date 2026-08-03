# VoiceOS Threat Registry

**Version:** 1.0
**Sprint:** Sprint-028
**Reference:** `docs/security/threat-model.md`

Each entry: ID · STRIDE category · Component · Description · Current controls · Residual risk · Acceptance status.

---

| ID | STRIDE | Component | Threat Description | Current Controls | Residual Risk | Status |
|----|--------|-----------|-------------------|-----------------|---------------|--------|
| T-01 | Spoofing | SIP Gateway | Caller-ID spoofing via forged SIP `From:` header allows impersonation of known customers | STIR/SHAKEN attestation (carrier-dependent); RBI-IDENTITY-VERIFY-FIRST gate | Low | Accepted |
| T-02 | Spoofing | API Platform | JWT forgery with elevated claims (tenant_id, roles) bypasses authorization | RS256 asymmetric signing; `alg: none` rejection; key rotation | Low | Accepted |
| T-03 | Spoofing | Service mesh | mTLS certificate spoofing on service-to-service calls by presenting forged certificate | VoiceOS internal PKI (Sprint-018); CA-signed certs required | Low | Accepted |
| T-04 | Spoofing | Admin Portal | Admin credential phishing; adversary impersonates admin user | mTLS client certificate required for admin portal; session scoping | Low | Accepted |
| T-05 | Tampering | Audit Logger | Adversary with DB write access modifies audit records to erase evidence | Append-only audit table (no UPDATE/DELETE grants); external SIEM | Low | Accepted |
| T-06 | Tampering | Event Bus (Redis) | Crafted Redis Stream events injected to trigger unauthorized actions or corrupt idempotency | Redis AUTH; mTLS; EventBus schema validation on consume | Medium | Accepted — monitoring in place |
| T-07 | Tampering | LLM Adapter | Prompt injection via customer utterance overrides agent instructions | System prompt hardening; Law of Authority; Policy Engine gate on LLM output | Medium | Accepted — cannot fully prevent; detection in place |
| T-08 | Tampering | Redis session store | Session state in Redis modified by adversary to alter call flow | Redis AUTH; per-tenant namespace isolation; session state signed | Low | Accepted |
| T-09 | Tampering | Consent Service | Replay of old consent-given event after consent revocation | Idempotency guard (Sprint-017); authoritative consent record never overwritten | Low | Accepted |
| T-10 | Repudiation | Audit Logger | AI decision made with no audit record; system later denies the decision was made | DecisionEnvelope logged per LLM response; trace_id links all events | Low | Accepted |
| T-11 | Repudiation | Conversation Engine | Call events dropped due to crash; audit trail incomplete; compliance cannot be proved | Event completeness checks (Sprint-020); `audit_events_total` Prometheus counter | Low | Accepted |
| T-12 | Info Disclosure | StructuredLogger | Developer logs a customer object including PII; unauthorized access to log aggregation | PIIScrubber in StructuredLogger; `check_pii_logs.py` CI gate | Low | Accepted |
| T-13 | Info Disclosure | API Platform (IDOR) | Cross-tenant data access: valid token for tenant A used to query tenant B's customer | `tenant_id` enforced on every repo query; TenantIsolationMiddleware | Low | Accepted |
| T-14 | Info Disclosure | LLM Adapter | Model extraction via thousands of crafted queries to extract weights or training data | Rate limiting per API key; response content filtering | Medium | Accepted — mitigated; detection in place |
| T-15 | Info Disclosure | Postgres | SQL injection via unsanitized API parameter exposes arbitrary DB rows | Parameterized psycopg2 queries throughout; no string interpolation in SQL | Low | Accepted |
| T-16 | Info Disclosure | OTel Collector / Jaeger | PII placed in span attributes by developer; exposed in trace storage | OTel Collector attribute allow-list processor; Jaeger access controls | Low | Accepted |
| T-17 | DoS | GPU Scheduler | VRAM exhaustion via thousands of concurrent LLM calls; all calls OOM | GPU Scheduler concurrency limits (Sprint-026); circuit breakers; token limits | Medium | Accepted — VRAM alerting; concurrency limits |
| T-18 | DoS | Media Gateway | RTP flood from external adversary delays legitimate audio packets | Admission control (Sprint-004); rate limiting per source IP; PLC | Low | Accepted |
| T-19 | DoS | LLM Adapter | LLM token bomb: crafted utterance generates 50k-token output, blocking GPU | `max_tokens` enforced per request (vLLM); per-session token budget | Low | Accepted |
| T-20 | DoS | API Platform | API key brute-force attack discovers valid credentials | Rate limiting per IP; lockout after N failures; key entropy ≥ 128 bits | Low | Accepted |
| T-21 | DoS | API Platform | WebSocket connection flood exhausts file descriptors and connection pool | Connection limits per tenant per IP; idle timeout; backpressure | Low | Accepted |
| T-22 | Elevation | RBAC | Tenant privilege escalation: caller supplies a different `tenant_id` in request body | `tenant_id` extracted from authenticated JWT (not caller-supplied); hard rules | Low | Accepted |
| T-23 | Elevation | Admin API | Adversary bypasses admin authentication to call internal admin endpoints | mTLS client certificate required; admin RBAC role; audit logging | Low | Accepted |
| T-24 | Elevation | Policy Engine | Malicious tenant config disables or overrides an RBI/DPDP hard rule | `hard_rule=True` structurally unweakenable (V4 Ch4 §4.13); policy inheritance | Low | Accepted |

---

## Statistics

| STRIDE Category | Count | Medium+ |
|----------------|-------|---------|
| Spoofing | 4 | 0 |
| Tampering | 5 | 2 (T-06, T-07) |
| Repudiation | 2 | 0 |
| Information Disclosure | 5 | 1 (T-14) |
| Denial of Service | 5 | 1 (T-17) |
| Elevation of Privilege | 3 | 0 |
| **Total** | **24** | **4** |

---

## Open Items (Medium Residual Risk)

| ID | Description | Remediation | Priority | Backlog Ref |
|----|-------------|-------------|----------|-------------|
| T-06 | Redis stream tampering | Add HMAC signing per-event in EventBus | P2 | TBD |
| T-07 | Prompt injection | Adversarial prompt detection layer; output classifier | P1 | TBD |
| T-14 | LLM model extraction | Semantic similarity rate limiting; output watermarking | P2 | TBD |
| T-17 | GPU VRAM exhaustion | VRAM headroom reservation per tenant; burst protection | P1 | TBD |
