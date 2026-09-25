# Level-2 Workstream 2 — Real Telephony Production Boundary

## Actual repository architecture

The live production path discovered on branch `claude/ssh-gpu-cpu-servers-y99fib` is Twilio-first:

`tenant/campaign/lead` → Redis tenant queue → `dialer_worker.js` → Twilio REST Call → Python `/voice` → single-use admission token → Twilio Media Streams WSS → `CallOrchestrator` → AudioSessionManager → AudioPreprocessor → VAD → STT → DialogueManager/ConversationEngine → TTS → Twilio media.

Twilio status callbacks currently terminate at BFF `/dialer/callback`, which signals the waiting dialer pipeline through Redis. Durable outbound attempts are written to PostgreSQL `call_attempts` before provider initiation.

A Twilio WebSocket adapter and SIP/RTP adapter protocol exist, but only the Twilio WebSocket entrypoint is bound by the CPU composition root. SIP is therefore implemented at adapter/protocol level but is not wired into the live listener.

## Capability baseline

| Capability | Status | Live path |
|---|---|---|
| Twilio outbound REST | IMPLEMENTED | WIRED INTO LIVE PATH |
| Twilio /voice webhook | IMPLEMENTED | WIRED INTO LIVE PATH |
| Twilio Media Streams WSS | IMPLEMENTED | WIRED INTO LIVE PATH |
| Provider signature + admission | IMPLEMENTED | WIRED INTO LIVE PATH |
| Tenant phone-number ownership | IMPLEMENTED | WIRED INTO LIVE PATH |
| Durable call attempts | IMPLEMENTED | WIRED INTO LIVE PATH |
| Crash reconciliation | IMPLEMENTED | WIRED INTO LIVE PATH |
| SIP transport | PARTIAL | NOT WIRED INTO LIVE PATH |
| Webhook lifecycle handling | PARTIAL | WIRED INTO LIVE PATH |
| Canonical call state machine | PARTIAL | PARTIALLY WIRED |
| Recording lifecycle | PARTIAL | WIRED WHEN ENABLED |
| Callback scheduling | PARTIAL | WIRED INTO BFF/Redis |
| Provider rate limiting | PARTIAL | WORKER TELEMETRY ONLY |
| Billing boundary | PARTIAL | PARTIAL |
| CRM event boundary | PARTIAL | PARTIAL |
| Runtime/production verification | UNKNOWN | RUNTIME EVIDENCE REQUIRED |

## Tenant isolation correction

The media gateway previously carried a process-wide `DEFAULT_TENANT_ID`. Workstream 2 now introduces `telephony_phone_numbers` as the provider-number ownership boundary.

- outbound calls resolve tenant ownership from `From`;
- inbound calls resolve tenant ownership from `To`;
- unknown/inactive numbers fail closed;
- the resolved tenant is embedded in the single-use WSS admission ticket;
- WSS uses the ticket tenant for consent, CRM context and call-session allocation;
- outbound caller-ID selection queries only active numbers belonging to the lead tenant, with campaign-specific precedence.

Provider secrets are not stored in the phone-number table.

## Remaining implementation areas

The remaining genuine W2 implementation gaps are authoritative webhook/state-transition handling, deterministic timeout classification, production recording storage lifecycle, callback timezone validation, provider rate limiting, and canonical durable call-event emission. These remain within W2 and do not require Level-3 AI work.
