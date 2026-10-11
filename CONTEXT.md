# VoiceOS — Session Context for New Claude Instances
# Last updated: 2026-09-10

Read this before reading anything else. It tells you what exists, where things run,
and what was last worked on. After reading this, read CLAUDE.md → PROJECT_STATUS.md →
implementation/CURRENT_SPRINT.md in that order.

---

## What VoiceOS Is

Enterprise AI voice-calling system. An AI agent calls customers, speaks Hindi/Hinglish,
collects loan payments, schedules callbacks, and escalates to humans when needed.
Built entirely on a self-managed, zero-cloud-account stack.

Owner / only user: **Prateek Das** (prateekdas7777@gmail.com). His Twilio account =
tenant `client-0` for all internal testing.

---

## Infrastructure — Current (as of 2026-09-10)

### GPU Server — Kaggle T4×2 (the ONLY GPU server)
- **Platform:** Kaggle notebook, account `mamatadas7777`, kernel slug `voiceos-sprint29-validation`
- **Hardware:** 2× NVIDIA Tesla T4 (16GB each)
- **What runs there:** Whisper STT · Qwen LLM (vLLM) · Veena TTS
- **Access:** No SSH. Push notebook via `kaggle kernels push` CLI, read logs via `kaggle kernels logs -f`.
- **Tunnels:** Each GPU service exposes its own Cloudflare Quick Tunnel (URL changes every restart).
  After kernel starts, get the 3 tunnel URLs from logs and put them in CPU `.env`:
  `LLM_BASE_URL`, `STT_BASE_URL`, `TTS_BASE_URL`
- **Push command:** `kaggle kernels push -p . --accelerator NvidiaTeslaT4`
  (DO NOT use workerSize in REST API — always gives P100)
- **Kernel files:** `deployment/gpu/kaggle/kernels/` — multiple kernels exist,
  main production one is `voiceos-sprint29-validation`

### CPU Server — Phone 2 (Android, Termux) [replacing cloud VPS]
- **Status:** Setup script created (`deployment/phone/setup_phone2.sh`), setup in progress
- **What runs there:** Twilio WebSocket media-gateway · Web API · PostgreSQL · Redis · cloudflared tunnel
- **Start command:** `python deployment/cpu/app.py --serve` (media-gateway on port 8010)
  Web API: `bash deployment/cpu/start_webapi.sh` (port 8001)
- **Tunnel:** cloudflared or localhost.run SSH tunnel → exposes port 8010 to Twilio
- **Twilio webhook:** `https://<tunnel-url>/voice` → Twilio Console TwiML App → Voice URL
- **SSH into Phone 2:** `ssh -p 8022 <local-ip>` (same WiFi) or Tailscale
- **Previous CPU:** Cloud VPS (IP changes every session — always ask user for current IP before SSH)

### Phone 1 — Development Machine (this phone)
- **Role:** Development, git, Claude Code sessions, also ran CPU services before phone 2
- **Branch:** Always push to `claude/ssh-gpu-cpu-servers-y99fib` — NEVER master/main

---

## Codebase Layout (key paths)

```
src/
  engines/sales/          ← Sales AI brain (phase 3 complete: pipeline_transitions,
                             production_actions, relationship_context, post_call_summary)
  services/
    conversation_engine/  ← Core orchestrator (ConversationEngine — THE runtime)
    media_gateway/        ← Twilio WebSocket entrypoint (twilio_ws_entrypoint.py)
    web_api/              ← REST API (call summaries, callbacks, auth)
deployment/
  cpu/
    app.py                ← Composition root — wires ALL real dependencies
    .env.example          ← Template for all env vars (copy to .env, fill in)
    start_webapi.sh       ← Starts web API on port 8001
    bootstrap.sh          ← Ubuntu/k8s bootstrap (NOT for Termux)
  gpu/kaggle/kernels/     ← All Kaggle GPU notebooks
  phone/
    setup_phone2.sh       ← One-shot Termux setup for phone 2 (new CPU server)
tests/
  unit/engines/sales/     ← Phase 3 tests (34 pass / 3 skipped cffi)
```

---

## What Was Last Built (September 2026)

### Phase 3 — Sales Production Action Layer (COMPLETE, commit 5d94842)

Wired real post-call actions into ConversationEngine:

1. **PipelineTransitionEngine** (`src/engines/sales/pipeline_transitions.py`)
   — FSM validates every LeadStage transition; terminal states (CONVERTED/DISQUALIFIED)
   block all moves; invalid transitions log ERROR but never abort the call turn.

2. **SalesProductionActionDispatcher** (`src/engines/sales/production_actions.py`)
   — SCHEDULE_FOLLOWUP → CallbackScheduler.schedule() with dedup per call_id
   — HUMAN_HANDOFF → returns signal; ConversationEngine fires escalate_call() AFTER
     the AI reply is spoken (not before, to avoid mid-sentence cut-off)

3. **RelationshipContextBuilder** (`src/engines/sales/relationship_context.py`)
   — Builds ≤5-line HISTORICAL prompt block from RelationshipMemory
   — Injected into LLM prompt after Step 3; labeled so LLM doesn't treat it as
     current-turn state

4. **PostCallSummaryRepository** (`src/libs/repositories/call_summary.py`)
   — Postgres `call_summaries` table with tenant isolation
   — save() upserts on call_id; get() scoped by tenant_id

5. **generate_post_call_summary()** (`src/engines/sales/post_call_summary.py`)
   — Pure serialization of SalesState → dict; no LLM, no DB; deterministic

6. **ConversationEngine wiring** (`src/services/conversation_engine/engine.py`)
   — Step 2b: FSM transition validation after CIL
   — Step 3b: RelationshipContext injected into prompt
   — Step 4b: SalesProductionActionDispatcher.dispatch() after RI-4 commit
   — Step 5b: escalate_call() fires after reply spoken
   — end_call(): generates + saves PostCallSummary to Postgres
   — start_call(): now correctly caches RelationshipMemory (previously discarded)

7. **API endpoint**: `GET /calls/{call_id}/summary` (tenant-scoped)

8. **Tests**: 34 pass / 3 skipped (cffi not available on Termux — repo tests skip)

### Phone 2 Setup (deployment/phone/setup_phone2.sh)
- Replaces cloud CPU VPS with second Android phone running Termux
- Installs: python, postgresql, redis, go, build tools
- Builds cloudflared from Go source (GitHub ARM64 binary is not PIE — fails on Android)
- Fallback tunnel: `ssh -R 80:localhost:8010 nokey@localhost.run`
- Creates `.env` template and `~/start_voiceos.sh` tmux launcher

---

## Environment Variables (critical ones)

See `deployment/cpu/.env.example` for full list. Most critical:

| Variable | What it is |
|----------|-----------|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Twilio credentials (client-0) |
| `LLM_BASE_URL` | Kaggle LLM tunnel URL (changes every kernel restart) |
| `STT_BASE_URL` | Kaggle STT tunnel URL |
| `TTS_BASE_URL` | Kaggle TTS tunnel URL |
| `PUBLIC_WS_BASE_URL` | cloudflared/localhost.run tunnel URL for this CPU (wss://) |
| `POSTGRES_PASSWORD` | Local PostgreSQL password |
| `DEFAULT_TENANT_ID` | `client-0` for Prateek's testing |
| `LENDER_NAME` | e.g. `Rajat Finance` — appears in AI greeting |

---

## Invariants You Must Know

- **RI-4:** DecisionEnvelope committed to Postgres BEFORE any external action (scheduler, handoff, TTS)
- **ConversationEngine boundary rule:** Files in `src/services/` cannot import from `src/engines/` at module level — all engine imports must be deferred `# noqa: PLC0415` inside method bodies
- **Twilio outbound media:** `media` WS message must contain only `payload` — extra fields trigger silent 31951 rejection
- **Kaggle push:** Always use `kaggle kernels push -p . --accelerator NvidiaTeslaT4`, never REST API workerSize

---

## Git

- Branch: `claude/ssh-gpu-cpu-servers-y99fib` (always, never master/main)
- Remote: `https://github.com/pateekdas7/VoiceOS.git`
- Latest commits (newest first):
  - `896f7a2` — chore: commit all pending GPU/CPU/phone deployment work
  - `7b3bf43` — fix(phone): build cloudflared from Go source (PIE fix)
  - `5d94842` — feat(sales): Phase 3 Production Action Layer complete

---

## What's Next (as of 2026-09-10)

1. **Finish phone 2 setup** — complete `bash setup_phone2.sh`, fill `.env`, run smoke-test
2. **Start Kaggle GPU kernel** — get fresh tunnel URLs, update `.env`
3. **Live call test** — place a real Twilio call end-to-end through phone 2 CPU + Kaggle GPU
4. **Sprint-029 Phase 2** — Founder validation with real call transcripts
5. **Sprint-030** — Pilot deployment

---

## Known Issues / Gotchas

- `onnxruntime` may fail to install on Termux ARM64 — VAD falls back to energy-based endpointing, calls still work
- `cffi/_cffi_backend` not available on Termux — PostCallSummaryRepository tests are skipped (not a bug)
- cloudflared GitHub ARM64 binary = non-PIE, fails on Android with `e_type: 2` — build via `go install` instead
- localhost.run SSH tunnel URL format: use `https://xxxx.lhr.life` for Twilio, `wss://xxxx.lhr.life` for PUBLIC_WS_BASE_URL
- CPU IP changes every session on cloud VPS — always ask user for current IP before SSH
