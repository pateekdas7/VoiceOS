# Sprint-004 — Media Gateway

**Epic:** E2 — Core Voice Runtime  
**Status:** ✅ Complete (2026-06-30)  
**Depends on:** Sprint-001, Sprint-002, Sprint-003  
**Blocks:** Sprint-005  

---

## Objective

Implement the Media Gateway service — the telephony ingress boundary of VoiceOS. This service receives inbound audio from telephony carriers (Twilio WebSocket, SIP/RTP) and emits a structured inbound audio stream to the Audio Session Manager. Auth-before-allocation is enforced: no audio session is allocated until the transport connection is authenticated.

---

## Architecture References

- Volume 1: Ch3 (Media Gateway — TransportAdapter protocol, Twilio, SIP/RTP, auth-before-allocation)
- Volume 6: Ch3 (Coding Standards), Ch4 (AR-2: auth-before-allocation)
- DocSuite-02: Interface Contracts (MediaGateway ↔ AudioSessionManager interface)

---

## Components to Implement

### `src/services/media-gateway/`

```
src/services/media-gateway/
├── __init__.py
├── service.py              (MediaGatewayService: lifecycle, adapter registry)
├── protocol.py             (TransportAdapter abstract base: connect, receive_frame, send_frame, disconnect)
├── adapters/
│   ├── __init__.py
│   ├── twilio_websocket.py (TwilioWebSocketAdapter: Twilio Media Streams WebSocket handler)
│   └── sip_rtp.py          (SIPRTPAdapter: SIP signaling + RTP media ingress)
├── auth.py                 (ConnectionAuthenticator: validates carrier credentials before session allocation)
├── session_gate.py         (SessionGate: enforces auth-before-allocation, tracks admitted sessions)
└── metrics.py              (Prometheus metrics: active_sessions, admission_rejections, bytes_received)
```

**Key behaviors:**

`TransportAdapter` (abstract):
- `async def authenticate(credentials: dict) -> AuthResult` — validates before any audio allocation
- `async def connect() -> None` — opens transport (called only after auth success)
- `async def receive_frame() -> AsyncIterator[AudioFrame]` — yields inbound frames
- `async def send_frame(frame: AudioFrame) -> None` — pushes outbound audio
- `async def disconnect() -> None`

`TwilioWebSocketAdapter`:
- Handles Twilio Media Streams `connected`, `start`, `media`, `stop` messages
- Decodes μ-law (PCMU) payload from `media.payload` (base64)
- Emits `AudioFrame(data=bytes, sample_rate=8000, encoding=MULAW, timestamp_ms=...)`
- Validates `AccountSid` and `AuthToken` from Twilio webhook signature before any processing
- Emits `AudioSessionStarted` event on session open

`SIPRTPAdapter`:
- Handles SIP INVITE, ACK, BYE signaling
- Opens RTP media stream on negotiated port
- Supports G.711 (PCMU/PCMA) and G.722
- Validates SIP `From` header authentication

`SessionGate` (enforces AR-2):
- Tracks `admitted_sessions: dict[CallId, AdmittedSession]`
- `admit(call_id, transport_adapter)` — only called after AuthResult.success
- `reject(call_id, reason)` — increments admission_rejections metric
- Enforces per-tenant concurrent session limits (from Policy Engine in future Sprint-017)

---

## Files Expected to Change

**New:** `src/services/media-gateway/` (all files above)  
**New:** `tests/unit/services/test_media_gateway.py`  
**New:** `tests/integration/services/test_media_gateway_integration.py`

---

## Acceptance Criteria

- [ ] `TwilioWebSocketAdapter` receives Twilio Media Streams webhook, decodes μ-law audio, emits `AudioFrame` objects
- [ ] Auth is enforced before session allocation: unauthenticated connection is rejected, no AudioFrame is emitted
- [ ] `SIPRTPAdapter` parses SIP INVITE and opens RTP socket for media
- [ ] `AudioSessionStarted` domain event is emitted on successful session admission
- [ ] `admission_rejections` Prometheus counter increments on auth failure
- [ ] TransportAdapter is an abstract protocol — neither adapter is coupled to MediaGatewayService internals

---

## Required Tests

**Unit:**
- `test_twilio_adapter_auth_success` — valid signature → auth passes, session admitted
- `test_twilio_adapter_auth_failure` — invalid signature → rejected, no AudioFrame emitted
- `test_twilio_frame_decode` — base64 μ-law payload correctly decoded to AudioFrame
- `test_session_gate_reject_unauthenticated` — SessionGate.reject() increments counter
- `test_sip_invite_parse` — SIP INVITE message parsed, RTP params extracted

**Integration:**
- `test_twilio_websocket_full_flow` — fake Twilio WebSocket sends start+media+stop, AudioFrames received by integration test consumer

---

## Definition of Done

- [ ] All AC items checked
- [ ] All tests pass
- [ ] CI green
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-005
