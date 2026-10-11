# GPU / Full-Deployment Testing Checklist

> These items require GPU hardware, real Twilio credentials, and live DB/Redis.
> Complete after all 16 phases are done and GPU+CPU access is available.

---

## Integration Tests (need real DB + Redis)

- [ ] Run `tests/unit/bff/test_phase4.js` against a live Postgres instance
  - processOneRow: all 7 outcome paths
  - crmMatchPhones: MATCHED / AMBIGUOUS / UNMATCHED
  - Upload handler: full CSV import with DB writes
  - Resume handler: crash-safe batch commit, CRM match, finalize

- [ ] Run full campaign lifecycle end-to-end:
  DRAFT → REVIEW → APPROVED → ACTIVE → PAUSED → ACTIVE → COMPLETED

- [ ] Lead upload → qualify → distribute → assign-pipeline flow

- [ ] Lead import resume from FAILED state (real rows_data in DB)

---

## Dialer Worker Tests (need Redis + Twilio)

- [ ] DialerWorker.start() boot sequence — heartbeat key, crash reconciliation
- [ ] CrashReconciler.reconcile() — stale INITIATED/IN_PROGRESS calls
  - Twilio real status fetch (production mode)
  - Worker-ID claim race (multi-worker safe via Redis NX)
- [ ] Pipeline._processLead() full loop — call initiation through completion
- [ ] CallSimulator — RINGING → IN_PROGRESS → outcome → completion signal
- [ ] TwilioDialer.initiate() — live Twilio call creation (staging account)
- [ ] Pipeline._waitForCompletion() — real Redis RPOP polling under load

---

## Twilio / HMAC (need real credentials)

- [ ] POST /dialer/callback with valid Twilio HMAC signature
- [ ] Replay attack prevention (idempotency_keys table)
- [ ] All call status events: initiated, ringing, answered, completed, failed, no-answer, busy

---

## GPU Health Endpoint

- [ ] GET /system/health with live STT/LLM/TTS probes
  - STT (Whisper) on port defined by AI_PORTS.STT
  - LLM (Ollama/vLLM) on port defined by AI_PORTS.LLM
  - TTS (Coqui/XTTS) on port defined by AI_PORTS.TTS
  - WireGuard tunnel: GPU_HOST env var must point to GPU machine

---

## Performance / Load (need full stack)

- [ ] Concurrent multi-pipeline dialing (3+ pipelines simultaneously)
- [ ] Lead queue throughput: 1000+ leads imported, qualified, queued
- [ ] Redis memory under sustained call load (MAX_CALL_DURATION_S × active calls)
- [ ] Crash recovery: kill worker mid-call, verify CrashReconciler repairs state

---

## Security Smoke Tests (need live environment)

- [ ] Tenant isolation: confirmed tenant B cannot access tenant A data via real DB
- [ ] JWT expiry: expired token is rejected by requireAuth
- [ ] HMAC bypass attempt: invalid signature returns 403 in production mode
- [ ] RBAC: TENANT_MEMBER cannot perform ADMIN-only actions

---

## Environment Variables Required for Full Deployment

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM_NUMBER=+91XXXXXXXXXX
PUBLIC_BFF_URL=https://bff.voiceos.ai
PUBLIC_WS_URL=wss://bff.voiceos.ai
GPU_HOST=<wireguard-ip>
POSTGRES_HOST=<db-host>
REDIS_HOST=<redis-host>
JWT_SECRET=<32-char-secret>
```

---

_Created during Phase 5 (Node.js Jest Test Suite). Revisit after Phase 16 completion._
