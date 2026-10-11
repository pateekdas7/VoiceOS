# CPU Runtime Recovery Specification
# Phase-P — New Server: root@205.147.102.94
# Source: recovered from git branch claude/ssh-gpu-cpu-servers-y99fib
# Created: 2026-08-22

---

## 1. RECOVERED ARCHITECTURE MAP

Every component recovered from source. Status key:
- **ACTIVE** — implemented, wired in composition root, used in live calls
- **STUB** — class exists, method bodies trivial/no-op
- **PARTIAL** — implemented but not fully wired

### 1.1 Telephony Transport Layer

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| Starlette WS App | `src/services/media_gateway/twilio_ws_entrypoint.py` | `create_twilio_media_stream_app()` | WS /twilio/media-stream, port 8010 | ACTIVE |
| CallOrchestrator | `src/services/media_gateway/twilio_ws_entrypoint.py` | `CallOrchestrator` | Per-call 3-task pipeline | ACTIVE |
| SharedCallDependencies | `src/services/media_gateway/twilio_ws_entrypoint.py` | `SharedCallDependencies` | Process-lifetime singletons | ACTIVE |
| TwilioWebSocketAdapter | `src/services/media_gateway/adapters/twilio_websocket.py` | `TwilioWebSocketAdapter` | Twilio Media Streams protocol parsing | ACTIVE |
| MediaGatewayService | `src/services/media_gateway/service.py` | `MediaGatewayService` | AR-2 call admission + adapter registry | ACTIVE |
| CallRecorder | `src/services/media_gateway/call_recorder.py` | `CallRecorder` | Per-call JSONL transcript + WAV recording | ACTIVE |

### 1.2 Audio Processing Layer

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| AudioSessionManagerService | `src/services/audio_session_manager/service.py` | `AudioSessionManagerService` | Jitter buffer + PLC, per-call AudioSession | ACTIVE |
| AudioPreprocessorService | `src/services/audio_preprocessing/service.py` | `AudioPreprocessorService` | 8kHz→16kHz upsampling | ACTIVE |
| VADEndpointingService | `src/services/vad_endpointing/service.py` | `VADEndpointingService` | Speech detection, endpoint detection, barge-in | ACTIVE |
| VADEngine | `src/services/vad_endpointing/vad_engine.py` | `VADEngine` | VAD model wrapper | ACTIVE |
| SileroVADModel | `src/services/vad_endpointing/vad_engine.py` | `SileroVADModel` | Production ONNX VAD (requires SILERO_VAD_MODEL_PATH) | ACTIVE |
| EnergyVADModel | `src/services/vad_endpointing/vad_engine.py` | `EnergyVADModel` | Energy-threshold fallback VAD | ACTIVE |
| EndpointDetector | `src/services/vad_endpointing/endpoint_detector.py` | `EndpointDetector` | Turn-end detection from VAD events | ACTIVE |
| BargeinDetector | `src/services/vad_endpointing/bargein_detector.py` | `BargeinDetector` | Barge-in during playback detection | ACTIVE |
| BackchannelDiscriminator | `src/services/vad_endpointing/backchannel.py` | `BackchannelDiscriminator` | Filter "hmm/ok" backchannels from real barge-ins | ACTIVE |
| AudioOutput | `src/services/playback/output.py` | `AudioOutput` | PCM→mu-law conversion for Twilio outbound | ACTIVE |
| PlaybackScheduler | `src/services/playback/scheduler.py` | `PlaybackScheduler` | Per-call bounded queue of AudioClauses; barge-in tracking | ACTIVE |

### 1.3 STT Layer (CPU→GPU HTTP)

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| STTService | `src/services/stt/service.py` | `STTService` | STT service wrapper | ACTIVE |
| WhisperHTTPAdapter | `src/services/stt/adapters/whisper_http_adapter.py` | `WhisperHTTPAdapter` | HTTP POST to GPU:8100/transcribe | ACTIVE |
| DialogueManager | `src/services/dialogue_manager/service.py` | `DialogueManager` | Assembles WordHypotheses into TurnInput | ACTIVE |

### 1.4 Conversation Intelligence Layer (CIL)

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| ResponsePlanningEngine | `src/engines/response_planning/engine.py` | `ResponsePlanningEngine` | CIL orchestrator (top-level) | ACTIVE |
| IntentEngine | `src/engines/intent/engine.py` | `IntentEngine` | Intent classification | ACTIVE |
| EntityExtractor | `src/engines/entity_extraction/engine.py` | `EntityExtractor` | Slot extraction | ACTIVE |
| EmotionIntelligenceEngine | `src/engines/emotion/engine.py` | `EmotionIntelligenceEngine` | Emotion detection | ACTIVE |
| RiskEngine | `src/engines/risk/engine.py` | `RiskEngine` | Risk flag scoring | ACTIVE |
| DialoguePolicyEngine | `src/engines/dialogue_policy/engine.py` | `DialoguePolicyEngine` | Policy constraint resolution | ACTIVE |
| StrategyEngine | `src/engines/strategy/engine.py` | `StrategyEngine` | Collections strategy selection | ACTIVE |
| GoalPlanner | `src/engines/goal_planner/engine.py` | `GoalPlanner` | Session goal tracking | ACTIVE |
| NegotiationEngine | `src/engines/negotiation/engine.py` | `NegotiationEngine` | PTP negotiation state machine | ACTIVE |
| EmpathyPlanner | `src/engines/empathy/engine.py` | `EmpathyPlanner` | Empathy directive selection | ACTIVE |
| AdaptiveConversationEngine | `src/engines/adaptive_conversation/engine.py` | `AdaptiveConversationEngine` | Adaptive pacing / loop detection | ACTIVE |
| DialogueResponseEngine | `src/engines/dialogue_response/engine.py` | `DialogueResponseEngine` | Scripted FSM golden path (Path-A Phase 6f) | ACTIVE |
| WorkingMemoryStore | `src/engines/memory/working/store.py` | `WorkingMemoryStore` | Redis-backed per-call state (4h TTL) | ACTIVE |
| RelationshipMemoryStore | `src/engines/memory/relationship/store.py` | `RelationshipMemoryStore` | Postgres-backed cross-call customer state | ACTIVE |

### 1.5 LLM / TTS Layer (CPU→GPU HTTP)

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| ConversationEngine | `src/services/conversation_engine/engine.py` | `ConversationEngine` | Top-level turn handler: CIL → LLM → TTS | ACTIVE |
| LLMService | `src/services/llm_runtime/service.py` | `LLMService` | LLM service wrapper | ACTIVE |
| vLLMAdapter | `src/services/llm_runtime/adapters/vllm_adapter.py` | `vLLMAdapter` | SSE streaming to GPU:8000/v1/chat/completions | ACTIVE |
| TTSService | `src/services/tts/service.py` | `TTSService` | TTS service wrapper | ACTIVE |
| VeenaAdapter | `src/services/tts/adapters/veena_adapter.py` | `VeenaAdapter` | Chunked HTTP to GPU:8200/synthesize | ACTIVE |
| GPUScheduler | `src/services/gpu_scheduler/scheduler.py` | `GPUScheduler` | VRAM ledger for admission control | ACTIVE |
| PromptBuilder | `src/engines/prompt_builder/builder.py` | `PromptBuilder` | Assembles LLM prompt from ResponsePlan | ACTIVE |

### 1.6 Data / Persistence Layer

| Component | File | Class | Purpose | Status |
|-----------|------|-------|---------|--------|
| Postgres (psycopg2) | `deployment/cpu/app.py` | `build_postgres_connection()` | 59 tables, alembic 0027 head | ACTIVE |
| Redis (raw client) | `deployment/cpu/app.py` | `build_raw_redis_client()` | EventBus, WorkingMemory, PolicyEngine | ACTIVE |
| EventBus | `src/libs/event_bus/bus.py` | `EventBus` | Redis Streams pub/sub | ACTIVE |
| PolicyEngineService | `src/services/policy_engine/service.py` | `PolicyEngineService` | Redis-cached policy rules | ACTIVE |
| IdempotencyGuard | `src/libs/idempotency/guard.py` | `IdempotencyGuard` | Postgres-backed idempotency tokens | ACTIVE |
| CustomerContextAssembler | `src/services/crm/context_assembler.py` | `CustomerContextAssembler` | Assembles CustomerContext from CRM/EMI/Loan repos | ACTIVE |
| PromiseToPayService | `src/services/collections/promise_to_pay.py` | `PromiseToPayService` | Persists PTP commitments to Postgres | ACTIVE |

### 1.7 Additional Services (non-call-path)

| Component | File | Purpose | Port | Status |
|-----------|------|---------|------|--------|
| Web API | `src/services/web_api/main.py` | Admin monitoring, Google OAuth, ops intelligence | 8001 | ACTIVE |
| BFF (Node.js) | `bff/` (not in src/) | Frontend backend-for-frontend | 8000 | ACTIVE |
| Frontend | `frontend/` | React/Next.js UI | 3000 | ACTIVE |

---

## 2. RUNTIME PATH TRACE

One complete Twilio call, from carrier audio to spoken reply:

```
TWILIO CARRIER
     │  μ-law PCM, 8kHz, 20ms frames, 160 bytes/frame
     │  WSS (TLS) → Cloudflare tunnel → nginx :80 → forward
     ▼
CPU NODE :8010  WS /twilio/media-stream
     │  Starlette WebSocketRoute → _endpoint()
     │  Auth: AR-2 via MediaGatewayService.admit_adapter()
     │     Twilio X-Twilio-Signature HMAC verified against PUBLIC_WS_BASE_URL
     ▼
CallOrchestrator.run()  [3 concurrent async tasks]

┌─── TASK A: _pump_inbound() ──────────────────────────────────────────────┐
│  TwilioWebSocketAdapter.receive_frame()                                   │
│     → _mulaw_frame_to_pcm16le()  [audioop.ulaw2lin — PCM16LE 8kHz]       │
│     → AudioSession.push_frame()  [jitter buffer, PLC]                    │
│     → AudioPreprocessorService.process_frame()  [8kHz→16kHz upsample]   │
│     → VADEndpointingService.process_frame()                              │
│          SileroVADModel or EnergyVADModel (fallback)                     │
│          emits: VADSpeechStart, VADSpeechEnd, BargeinDetected            │
│     On VADSpeechStart  → open asyncio.Queue, set turn_ready event        │
│     On VADSpeechEnd    → push None sentinel to close turn stream         │
│     On BargeinDetected → PlaybackScheduler.flush()                       │
└───────────────────────────────────────────────────────────────────────────┘

┌─── TASK B: _run_turns() ─────────────────────────────────────────────────┐
│  await turn_ready                                                         │
│  STTService.transcribe_stream(frame_queue, language="hi")                │
│     WhisperHTTPAdapter._transcribe_gen()                                 │
│       1. GPUScheduler.request_allocation("stt", 6144 MB)                 │
│       2. Collect all PCM16LE 16kHz frames into buffer                    │
│       3. POST http://GPU_NODE_HOST:8100/transcribe                       │
│            {"audio_b64": base64(PCM16LE), "language": "hi", "beam_size": 5}
│       4. Yield WordHypothesis per word from response                     │
│       5. GPUScheduler.release_allocation()                               │
│  DialogueManager.ingest_stream(word_stream) → TurnInput                  │
│                                                                           │
│  [skip if transcript is empty — noise/silence turn]                      │
│                                                                           │
│  ConversationEngine.handle_turn(turn, playback, context)                 │
│     CIL ResponsePlanningEngine.run(TurnInput) → ResponsePlan             │
│       IntentEngine → EntityExtractor → EmotionEngine → RiskEngine        │
│       DialoguePolicyEngine → StrategyEngine → GoalPlanner                │
│       NegotiationEngine → EmpathyPlanner → AdaptiveConversationEngine   │
│     DialogueResponseEngine.next_reply(ResponsePlan) [golden path FSM]   │
│        OR LLMService (fallback if dialogue_response is None)             │
│     [If LLM path:]                                                       │
│       PromptBuilder.build(ResponsePlan, CustomerContext) → prompt        │
│       vLLMAdapter.generate_stream()                                      │
│         GPUScheduler.request_allocation("llm", 16384 MB)                │
│         POST http://GPU_NODE_HOST:8000/v1/chat/completions (SSE)         │
│         model: qwen2.5-7b-instruct-fp8, temp=0.3, top_p=0.9             │
│         Yield TokenChunk                                                  │
│     VeenaAdapter.synthesize_stream(text_chunks, voice_config)            │
│       ClauseSplitter.feed() → clause text segments                       │
│       Per clause:                                                         │
│         GPUScheduler.request_allocation("tts", 7974 MB)                 │
│         POST http://GPU_NODE_HOST:8200/synthesize (chunked)              │
│           {"text": str, "speaker": "kavya", "voice_config": {...}}       │
│         Yield AudioClause per 85.33ms PCM16LE 24kHz chunk (4096 bytes)  │
│         PlaybackScheduler.enqueue(clause)                                │
│     Returns list[AudioClause]                                            │
│                                                                           │
│  _send_clauses(clauses):                                                  │
│    Per clause:                                                            │
│      AudioOutput.convert(clause, fmt="ulaw")                             │
│        PCM16LE 24kHz → resample 8kHz → mu-law encode                    │
│      TwilioWebSocketAdapter.send_frame() → outbound JSON queue           │
│      PlaybackScheduler.dequeue_nowait()  [keeps bounded queue accurate]  │
└───────────────────────────────────────────────────────────────────────────┘

┌─── TASK C: _pump_outbound() ─────────────────────────────────────────────┐
│  Loop: TwilioWebSocketAdapter.drain_outbound()                           │
│    → websocket.send_text(json_msg)                                       │
│  Twilio Media Streams JSON text frames → carrier → caller's phone        │
└───────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Call-Open Greeting Path (Phase 6g)

Before the 3-task loop starts:
```
ConversationEngine.build_greeting(context) → greeting text
ConversationEngine.speak_scripted_text(greeting, playback)  [TTS only, no LLM]
   → VeenaAdapter → GPU:8200 → AudioClause stream
Concurrent: PlaybackScheduler.dequeue_nowait() → send_clause() → Twilio
asyncio.wait_for(timeout=greeting_timeout_s=60.0)
   [timeout → log error, proceed without greeting]
```

---

## 3. CPU↔GPU HTTP CONTRACTS

These match the golden baseline (`deployment/gpu/golden_baseline.yaml`).

### 3.1 STT — GPU:8100

```
Endpoint:  POST http://{GPU_NODE_HOST}:8100/transcribe
Health:    GET  http://{GPU_NODE_HOST}:8100/health/ready

Request:
  {
    "audio_b64": "<base64-encoded PCM16LE 16kHz mono>",
    "language": "hi",        # BCP-47, default "hi"
    "beam_size": 5
  }

Response:
  {
    "words": [
      {"word": str, "confidence": float, "start_ms": int, "end_ms": int, "is_final": bool},
      ...
    ],
    "language": str,
    "duration_ms": int,
    "latency_ms": int
  }

VRAM reserved: 6144 MB (WhisperHTTPAdapter._WHISPER_VRAM_MB)
Model:        mobiuslabsgmbh/faster-whisper-large-v3-turbo
```

### 3.2 LLM — GPU:8000

```
Endpoint:  POST http://{GPU_NODE_HOST}:8000/v1/chat/completions
Health:    GET  http://{GPU_NODE_HOST}:8000/health

Request: OpenAI chat completions format
  {
    "model": "qwen2.5-7b-instruct-fp8",
    "messages": [...],
    "stream": true,
    "temperature": 0.3,
    "top_p": 0.9,
    "max_tokens": <from ResponsePlan>
  }

Response: Server-Sent Events (SSE) token stream
  data: {"choices": [{"delta": {"content": "..."}}]}
  ...
  data: [DONE]

VRAM reserved: 16384 MB (vLLMAdapter._QWEN_VRAM_MB)
Model:         RedHatAI/Qwen2.5-7B-Instruct-FP8-dynamic
Engine:        vLLM 0.24.0
```

### 3.3 TTS — GPU:8200

```
Endpoint:  POST http://{GPU_NODE_HOST}:8200/synthesize
Health:    GET  http://{GPU_NODE_HOST}:8200/health/ready

Request:
  {
    "text": str,
    "speaker": "kavya",
    "voice_config": {
      "pitch_shift": float,
      "rate_scale": float,
      "energy_scale": float,
      "pause_ms_after_clause": int,
      "language": str
    }
  }

Response: Transfer-Encoding: chunked
  Body: raw PCM16LE 24kHz mono bytes
  Chunk size: 4096 bytes = 85.33ms per chunk

VRAM reserved: 7974 MB (VeenaAdapter._VEENA_VRAM_MB)
Model:         maya-research/Veena (3B BF16)
SNAC codec:    hubertsiuzdak/snac_24khz
```

---

## 4. AUDIO CONTRACT

```
Stage                  Format              Rate    Frame/Chunk   Bytes
─────────────────────────────────────────────────────────────────────────
Twilio inbound wire    μ-law (PCMU)        8kHz    20ms          160
After ulaw2lin decode  PCM16LE             8kHz    20ms          320
After AudioPreprocessor PCM16LE           16kHz   20ms          640
STT input (buffered)   PCM16LE            16kHz   full utterance variable
TTS output (from GPU)  PCM16LE            24kHz   85.33ms       4096
After resample+encode  μ-law (PCMU)        8kHz   20ms          160
Twilio outbound wire   μ-law (PCMU)        8kHz    20ms          160
```

---

## 5. REQUIRED ENVIRONMENT VARIABLES

See `deployment/cpu/.env.example` for full documentation.

### 5.1 Mandatory (no defaults)

```bash
# Postgres
POSTGRES_PASSWORD=<vault-fetched>   # OR use POSTGRES_DSN
# POSTGRES_DSN=postgresql://voiceos:<pw>@127.0.0.1:5432/voiceos

# GPU connection (CRITICAL: Kaggle IP changes each session)
GPU_NODE_HOST=<kaggle-public-ip>

# Twilio
TWILIO_ACCOUNT_SID=<from-twilio-console>
TWILIO_AUTH_TOKEN=<from-twilio-console>

# Cloudflare tunnel URL (changes per session unless named tunnel)
PUBLIC_WS_BASE_URL=wss://<tunnel-hostname>.trycloudflare.com
```

### 5.2 With defaults

```bash
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=                     # empty = no auth
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=voiceos
POSTGRES_USER=voiceos
MEDIA_GATEWAY_PORT=8010
STT_LANGUAGE=hi
LENDER_NAME=Rajat Finance
GREETING_TIMEOUT_S=60.0
EVENT_BUS_STREAM=voiceos-events
GPU_VRAM_MB=49140                   # ledger budget (not actual GPU VRAM)
```

### 5.3 Optional / Production

```bash
VAULT_ADDR=http://127.0.0.1:8200   # HashiCorp Vault
VAULT_TOKEN=<root-or-app-token>
SILERO_VAD_MODEL_PATH=/opt/voiceos/models/silero_vad.onnx
CALL_RECORDING_DIR=/opt/voiceos/recordings
DEFAULT_TENANT_ID=tenant-default
WEBAPI_PORT=8001
```

---

## 6. NEW SERVER INVENTORY (205.147.102.94)

Discovered during Phase-P Step 1 (2026-08-22):

```
OS:           Ubuntu 24.04.4 LTS
CPU:          AMD EPYC 7542, 4 vCPU
RAM:          6.1 GB total
Disk:         24 GB total, ~65% used (~8.4 GB free)
Python:       3.12.3 (pre-installed at /usr/bin/python3)
Docker:       NOT installed
systemd:      255 (present)
SSH:          port 22, key: ~/.ssh/id_ed25519
Private IP:   10.0.2.2
Public IP:    205.147.102.94

Pre-installed monitoring agents (unusual for CPU-only):
  node_exporter  :9100
  dcgm-exporter  :9400
  nvidia-dcgm    (daemon — GPU monitoring; server has no GPU)
```

**Constraints vs old server:**
- Old: 8 vCPU / 12 GB RAM; New: 4 vCPU / 6.1 GB RAM — half the resources
- No Kubernetes viable (old node had 26 pods incl. full observability stack)
- Disk headroom is tight: pip deps + Postgres data + recordings need space
- Network restriction status: **UNKNOWN — must test before `pip install`**

---

## 7. RECOVERY GAP REPORT

| Gap | Severity | Description | Mitigation |
|-----|----------|-------------|-----------|
| GPU_NODE_HOST is dynamic | HIGH | Kaggle assigns a new IP each session | Update `.env` at start of each Kaggle session; consider Cloudflare tunnel on GPU side |
| PUBLIC_WS_BASE_URL changes | HIGH | Cloudflare quick tunnel URL regenerates | Use `cloudflared tunnel create` (named, persistent) instead of quick tunnel |
| Network restriction unknown | HIGH | Old server had DPI blocking PyPI/GitHub/Docker Hub at TLS/SNI layer | Test `curl -I https://pypi.org` before pip install; if blocked, use offline pip pattern |
| Silero VAD model absent | MEDIUM | No ONNX file at SILERO_VAD_MODEL_PATH | Download `silero_vad.onnx` from silero-team/silero-vad on HuggingFace; or use EnergyVADModel fallback for initial validation |
| Vault not provisioned | MEDIUM | bootstrap.sh installs Vault but it needs init + unseal | Run `deployment/cpu/restore.sh` vault bootstrap section; or set POSTGRES_DSN directly for initial bring-up |
| MongoDB 7.0 on Ubuntu 24.04 | MEDIUM | MongoDB 7.0 repo may not have Ubuntu 24.04 (Noble) packages yet | Check: `curl https://repo.mongodb.org/apt/ubuntu/dists/noble/` — if absent, use 7.0.x from apt.mongodb.org or skip MongoDB for call path (not on hot path) |
| Disk space tight | MEDIUM | ~8.4 GB free; Python venv + pip deps ~2 GB; Postgres needs space | Monitor `df -h` during install; recordings dir should be on separate mount if disk fills |
| RAM headroom | MEDIUM | 6.1 GB for OS + Postgres + Redis + MongoDB + Python app | MongoDB is off hot-path; consider `--no-install-recommends` on apt; disable swap compressors |
| Docker not installed | LOW | bootstrap.sh installs Docker; old K8s topology not reproducible | Run without Docker/K8s initially; use systemd services directly |
| Kubernetes not viable | LOW | 4 vCPU/6.1 GB cannot run 26-pod K8s stack from old node | Deploy with systemd only; Kubernetes is deferred |
| nvidia-dcgm on CPU node | INFO | Provider pre-installed GPU monitoring on a CPU-only VM | Benign; leave in place; don't let it confuse GPU_NODE_HOST env var |

---

## 8. RECONSTRUCTION PROCEDURE

### Pre-flight checks (run before bootstrap.sh)

```bash
# 1. Check network access
curl -I https://pypi.org/simple/ 2>&1 | head -5
curl -I https://github.com 2>&1 | head -5

# 2. Check disk
df -h /

# 3. Check Python
python3 --version      # want 3.12.x
```

**If PyPI/GitHub are blocked:** use offline pip pattern from CPU_NODE_STATE.md:
```bash
# On a machine with internet access:
pip download -r requirements.txt -d /tmp/pip_cache/
# scp /tmp/pip_cache/ root@205.147.102.94:/tmp/pip_cache/
# On the server:
pip install --no-index --find-links=/tmp/pip_cache/ -r requirements.txt
```

### Step 1: Bootstrap system

```bash
ssh -i ~/.ssh/id_ed25519 root@205.147.102.94
cd /tmp
# Clone repo (if network allowed) OR scp the repo tarball
git clone https://github.com/pateekdas7/VoiceOS.git /opt/voiceos/app
# OR: scp -i ~/.ssh/id_ed25519 voiceos.tar.gz root@205.147.102.94:/tmp/
# tar xzf /tmp/voiceos.tar.gz -C /opt/voiceos/app

cd /opt/voiceos/app
git checkout claude/ssh-gpu-cpu-servers-y99fib
# Verify git commit
git log --oneline -1   # should be 1d36425 or newer

# Run bootstrap (installs Redis, Postgres, Python venv, directories)
# IMPORTANT: bootstrap.sh installs MongoDB and Docker — skip those sections
# if network is restricted or disk is tight. The call path does NOT need MongoDB.
bash deployment/cpu/bootstrap.sh
```

### Step 2: Python environment

```bash
source /opt/voiceos/venv/bin/activate
cd /opt/voiceos/app
pip install -e .        # reads pyproject.toml

# Smoke-check imports
python -c "from src.services.media_gateway.twilio_ws_entrypoint import create_twilio_media_stream_app; print('OK')"
```

### Step 3: Configure .env

```bash
cp deployment/cpu/.env.example /opt/voiceos/app/deployment/cpu/.env
# Edit .env — fill in:
#   POSTGRES_PASSWORD (or POSTGRES_DSN)
#   TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
#   PUBLIC_WS_BASE_URL (cloudflare tunnel URL)
#   GPU_NODE_HOST (update each Kaggle session)
#   GPU_VRAM_MB=49140  (ledger budget — leave at default)
```

### Step 4: Database migrations

```bash
source /opt/voiceos/venv/bin/activate
cd /opt/voiceos/app
# Load env
set -a; source deployment/cpu/.env; set +a
# Run Alembic migrations (0000 → 0027)
alembic upgrade head
# Verify
alembic current   # should show 0027
```

### Step 5: MongoDB indexes (optional — not on hot call path)

```bash
python deployment/cpu/restore.sh   # section: MongoDB indexes
# OR skip MongoDB entirely for initial call validation
```

### Step 6: Start services

```bash
# Media Gateway (Twilio WS, port 8010)
source /opt/voiceos/venv/bin/activate
set -a; source /opt/voiceos/app/deployment/cpu/.env; set +a
python /opt/voiceos/app/deployment/cpu/app.py --serve

# Smoke test (construction only, no live call)
python /opt/voiceos/app/deployment/cpu/app.py --smoke-test
```

### Step 7: Cloudflare tunnel

```bash
# Quick tunnel (URL changes each restart)
cloudflared tunnel --url http://localhost:8010

# Update PUBLIC_WS_BASE_URL in .env with the new URL
# Restart app.py --serve
```

### Step 8: Validation

```bash
# 1. Health check (app must be running)
curl http://localhost:8010/health  # 404 expected — no health route; app responds to WS only

# 2. Smoke test
python deployment/cpu/app.py --smoke-test
# Expected: "OK — ConversationEngine constructed with every real dependency wired."
# Expected: "OK — PromiseToPayService constructed..."
# Expected: "OK — SharedCallDependencies constructed..."

# 3. STT reachability (GPU must be running)
curl http://$GPU_NODE_HOST:8100/health/ready   # → {"status": "ready"}
curl http://$GPU_NODE_HOST:8000/health          # → {"status": "ok"}
curl http://$GPU_NODE_HOST:8200/health/ready    # → {"status": "ready"}

# 4. Place test call via Twilio
# (requires Twilio number and phone, same as Call-002 procedure)
```

---

## 9. SYSTEMD SERVICE TOPOLOGY (target)

```
voiceos-media-gateway.service
  ExecStart: /opt/voiceos/venv/bin/python /opt/voiceos/app/deployment/cpu/app.py --serve
  Port: 8010
  Requires: postgresql.service redis.service

voiceos-webapi.service
  ExecStart: /opt/voiceos/venv/bin/uvicorn src.services.web_api.main:create_app --factory --host 0.0.0.0 --port 8001
  Port: 8001
  Requires: postgresql.service

nginx.service
  Port 80 → forward to :8010 (and :8001, :3000)

cloudflared.service
  Forward tunnel → :8010 (PUBLIC_WS_BASE_URL)
```

---

## 10. NETWORK TOPOLOGY

```
Caller's phone
     ↓ PSTN
Twilio (cloud)
     ↓ WSS (TLS) Twilio Media Streams
Cloudflare tunnel
     ↓ HTTP WS → CPU node :8010
VoiceOS Media Gateway (CPU node)
     ↓ HTTP (intranet or internet)
GPU node :8100 (STT)
GPU node :8000 (LLM)
GPU node :8200 (TTS)
     ↓ audio back up same path
CPU node → Cloudflare → Twilio → caller
```

Note: GPU node is currently Kaggle (ephemeral public IP). In production this should be
a WireGuard or private-network link; currently it is open HTTP over the public internet.

---

## 11. KNOWN LIMITATIONS ON NEW SERVER

1. **Resource constraint**: 4 vCPU / 6.1 GB RAM vs old server's 8 vCPU / 12 GB RAM. Running Postgres + Redis + MongoDB + Python app simultaneously may be tight. Monitor RSS and page faults.

2. **No Kubernetes**: Full observability stack (Prometheus, Grafana, Jaeger, Loki, etc.) from old node is not reproducible on 4 vCPU. Use structured logs only initially.

3. **GPU_NODE_HOST is ephemeral**: Each Kaggle session gives a new IP. Before placing any call, update `GPU_NODE_HOST` in `.env` and restart `app.py --serve`.

4. **No WireGuard**: CPU↔GPU traffic goes over public internet (HTTP, not HTTPS). Acceptable for validation; not acceptable for production with real PII.

5. **Disk**: 24 GB with 65% already used. VoiceOS venv + pip deps ≈ 2 GB; Postgres data grows. Prune any pre-installed files if needed.

---

## 12. RECOVERY SOURCE

- Git repo: `https://github.com/pateekdas7/VoiceOS.git`
- Branch: `claude/ssh-gpu-cpu-servers-y99fib`
- Head commit at time of recovery: `1d36425` (2026-08-22)
- Golden GPU baseline: `deployment/gpu/golden_baseline.yaml` (DO NOT MODIFY)
- Previous CPU node state: `deployment/CPU_NODE_STATE.md`
- Composition root: `deployment/cpu/app.py`
- Bootstrap: `deployment/cpu/bootstrap.sh`
- Restore: `deployment/cpu/restore.sh`
- Env template: `deployment/cpu/.env.example`
