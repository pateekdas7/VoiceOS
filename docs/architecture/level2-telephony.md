# Level-2 Workstream 2 — Real Telephony Production Layer

## Repository-discovered architecture

Current Twilio path on this branch:

`tenant/campaign/lead`
→ Redis tenant queue
→ `dialer_worker.js`
→ Twilio Calls API
→ Python `/voice` webhook
→ signed admission token
→ Twilio Media Streams `/twilio/media-stream`
→ `CallOrchestrator`
→ AudioSessionManager / preprocessing / VAD
→ STT → existing ConversationEngine → TTS
→ Twilio media
→ BFF `/dialer/callback`
→ durable `call_attempts` + `active_calls` + Redis completion signal.

### Boundary classification

| Capability | Status | Live path |
|---|---|---|
| Twilio outbound | IMPLEMENTED | WIRED INTO LIVE PATH |
| Twilio /voice | IMPLEMENTED | WIRED INTO LIVE PATH |
| Media Streams WSS | IMPLEMENTED | WIRED INTO LIVE PATH |
| Signed webhook/admission | IMPLEMENTED | WIRED INTO LIVE PATH |
| Tenant-owned phone mapping | IMPLEMENTED | WIRED INTO LIVE PATH |
| Durable call attempts | IMPLEMENTED | WIRED INTO LIVE PATH |
| Canonical callback transition guard | IMPLEMENTED | WIRED INTO LIVE PATH |
| SIP | PARTIAL | NOT WIRED INTO LIVE LISTENER |
| Recording production lifecycle | PARTIAL | RUNTIME/STORAGE EVIDENCE REQUIRED |
| Provider CPS limiting | IMPLEMENTED | WIRED INTO WORKER |
| Callback timezone policy | PARTIAL | NEEDS CANONICAL TZ VALIDATION |
| Billing event boundary | PARTIAL | NO COMPLETE BILLING IMPLEMENTATION |
| CRM event boundary | PARTIAL | POST-CALL DATA EXISTS; CONTRACT VERIFICATION PENDING |
| Real carrier runtime | UNKNOWN | RUNTIME EVIDENCE REQUIRED |

## Tenant isolation

Provider phone-number ownership is persisted in `telephony_phone_numbers`. The media gateway resolves outbound `From` and inbound `To` to the owning tenant and fails closed for an inactive/unassigned number. The resulting tenant is bound to the one-time WSS admission ticket. Outbound caller-ID selection is tenant/campaign scoped.

## Webhook lifecycle

The BFF validates Twilio HMAC when credentials are configured, requires the provider call SID, correlates it to `call_attempts` with a row lock, rejects tenant/lead/pipeline mismatches, normalizes provider statuses, suppresses backwards/out-of-order transitions, updates durable attempt/active-call state, and emits the existing Redis completion event only after terminal-state idempotency succeeds.

## Workstream boundary

This document describes the production telephony boundary only. It does not implement campaign scheduler/concurrency architecture, CRM implementation, billing implementation, or Level-3 conversation intelligence.


## W2 continuation — production boundary slices

### Recording lifecycle
The pre-existing CallRecorder is local capture instrumentation. W2 now adds durable recording metadata in PostgreSQL, tenant-scoped lifecycle states (CREATED → PROCESSING → RETAINED → DELETED, with FAILED), private object-storage abstraction, production S3 backend with server-side encryption and presigned GETs, filesystem test/local backend with no public URL, tenant authorization, provider-event idempotency, unknown-CallSID fail-closed handling, retention cleanup, and deletion retry state. Production startup rejects filesystem recording storage when NODE_ENV=production.

The capture artifacts are bundled after call teardown and associated back to the canonical call attempt when the provider CallSID is present. Recording finalization failure is persisted as FAILED rather than silently reported as available.

### Callback timezone boundary
Callback scheduling now requires a valid IANA timezone for naive local timestamps, or accepts an explicit UTC offset. Campaign timezone takes precedence over tenant timezone; an explicit callback_timezone overrides both. Callback instants are persisted/queued as UTC while local working-hour checks use the selected IANA timezone. Invalid/nonexistent DST wall times are rejected, excluded dates and allowed weekdays are enforced, and missing campaign timezone causes the dialer schedule verifier to fail closed.

### Provider failure contract
A canonical provider failure classifier now distinguishes transient/network, rate-limit, timeout, busy, no-answer, voicemail, provider-failed, invalid destination, authentication/configuration, permanent-provider, and unknown failures. Retryability and next action are part of the contract and are persisted on call_attempts. Existing bounded retry behavior is only invoked when the classification is retryable; no W3 scheduler is introduced.

### Webhook hardening
The Twilio status callback now has a 5-second PostgreSQL statement timeout, bounded Redis completion operations, structured outcome/latency metrics, spoof/configuration/malformed/unknown-call/tenant-mismatch/out-of-order classifications, and preserves the existing transactional/idempotent completion boundary. No AI work runs inside the provider callback.

### Telephony observability
The BFF now exposes bounded lifecycle-state counters, webhook outcome/failure counters, webhook latency sum/count, and call-setup latency sum/count. Labels are restricted to finite lifecycle/outcome/reason dimensions; no tenant IDs, phone numbers, CallSIDs, or other raw PII are metric labels.

### Canonical downstream event boundary
telephony_call_events is the durable W2 contract for future Billing/CRM consumers. Event identity is deterministic per tenant + provider CallSID + lifecycle state; the event carries schema version, tenant/campaign/lead/call/attempt identity, provider CallSID, lifecycle state, outcome, duration, recording/callback references, event timestamp, correlation ID, and sequence number. The webhook transaction persists the event before the existing Redis completion signal. This is a W2 event boundary only; no Billing or CRM business logic is implemented.
