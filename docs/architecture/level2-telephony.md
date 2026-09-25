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
