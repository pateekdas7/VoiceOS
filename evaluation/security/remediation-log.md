# Security Remediation Log — Sprint-028 Phase 2

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Companion to:** `evaluation/security/pen-test-report.md`
**Last updated:** 2026-07-11

---

## Report Metadata

| Field | Value |
|---|---|
| Date created | 2026-07-11 |
| Last updated | 2026-07-11 |
| Status | **OPEN — 5 active findings** |
| Source report | `evaluation/security/pen-test-report.md` |

---

## Remediation Log

| Finding ID | Severity | Title | Remediation | Owner | Target | Status |
|---|---|---|---|---|---|---|
| PEN-001 | HIGH | STT `/transcribe`: No authentication | Deploy API gateway (nginx/envoy/Kong) with bearer token or mTLS enforcement in front of all three GPU inference endpoints. Inference endpoints should not be directly reachable from outside the service mesh. | Platform/Infra | Pre-alpha canary | OPEN |
| PEN-002 | HIGH | TTS `/synthesize`: No authentication | Same fix as PEN-001 — API gateway with auth middleware. | Platform/Infra | Pre-alpha canary | OPEN |
| PEN-003 | HIGH | LLM `/v1/chat/completions`: No authentication | Same fix as PEN-001. vLLM supports `--api-key` flag for token-based auth — enable in production config. | Platform/Infra | Pre-alpha canary | OPEN |
| PEN-004 | MEDIUM | LLM Prompt injection (inconclusive) | Run dedicated red-team prompt injection test using Garak or PyRIT with full-response capture. If compromise is confirmed, add system-prompt hardening and output filtering layer before LLM response reaches TTS pipeline. | Security / ML | Sprint-029 | OPEN |
| PEN-005 | MEDIUM | TTS speaker field: no allowlist validation | Add `ALLOWED_SPEAKERS = frozenset({"kavya"})` (or tenant-configured allowlist) to `deployment/gpu/services/tts/server.py`. Return HTTP 422 with `{"error": "unknown speaker"}` for any speaker not in allowlist. Resolves PEN-005, PEN-006, PEN-007 in one code change. | Engineering | Sprint-029 | OPEN |
| PEN-008 | LOW | STT: No audio duration limit | Add max-duration check in `deployment/gpu/services/stt/server.py`: reject audio clips > 30s (480,000 samples at 16kHz) with HTTP 413. Alternatively enforce at API gateway layer via request body size limit. | Engineering | Sprint-029 | OPEN |
| PEN-009 | LOW | TTS: No text length limit | Add `if len(text) > MAX_TTS_CHARS: raise HTTPException(422, ...)` in `deployment/gpu/services/tts/server.py`. Recommended limit: 1,000 chars (~30-60s synthesis). | Engineering | Sprint-029 | OPEN |

---

## Priority Order

1. **PEN-001 / PEN-002 / PEN-003 (HIGH)**: Must be resolved before any external-facing alpha traffic. API gateway deployment is a prerequisite, not a follow-up. Can be fast-tracked with nginx + JWT middleware in front of GPU node.
2. **PEN-005 (MEDIUM)**: Single-line code fix in TTS server. Should be batched with next GPU node deployment.
3. **PEN-008 / PEN-009 (LOW)**: Code fixes in STT/TTS servers. Low risk in isolated staging environment; medium risk if staging is exposed to internet.
4. **PEN-004 (MEDIUM)**: Requires tooling and dedicated testing session. Not blocking for internal alpha but required before external user access.

---

## Sign-off

| Role | Status | Notes |
|---|---|---|
| Security Lead | **PENDING** | Review findings and approve remediation timelines |
| Engineering Lead | **PENDING** | Assign PEN-005, PEN-008, PEN-009 to Sprint-029 |
| Platform/Infra | **PENDING** | API gateway deployment plan for PEN-001-003 |
