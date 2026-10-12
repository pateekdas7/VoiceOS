# Blue-Green GPU Model Deployment Runbook

**Scope:** Replacing STT (Whisper), LLM (Qwen vLLM), or TTS (Veena) models on the GPU node without call interruption.

---

## 1. Prerequisites

- New model weights downloaded and validated on the GPU node
- Sufficient VRAM headroom to run both old and new model simultaneously during the swap window
- `GPU_SHARED_SECRET` rotated if the swap also involves a security key rotation

---

## 2. Procedure (per service)

### Step 1 — Warm up the new model on an alternate port

Start the new model version on a different port than the currently-serving one.

```bash
# Example: new Whisper model on port 8101 (live is 8100)
cd /opt/voiceos/gpu
GPU_SHARED_SECRET=<secret> python stt/server.py --model-path /models/whisper-v4 --port 8101

# Example: new vLLM model on port 8001 (live is 8000)
vllm serve /models/qwen3-7b-fp8 \
  --port 8001 \
  --served-model-name qwen3-7b-fp8 \
  --dtype auto \
  --gpu-memory-utilization 0.50
```

### Step 2 — Health-check the new model

Verify the new model responds correctly before switching traffic.

```bash
# STT health check
curl -s http://localhost:8101/health | jq .

# LLM health check
curl -s http://localhost:8001/v1/models | jq '.data[].id'

# TTS health check
curl -s http://localhost:8201/health | jq .
```

Run at least 3 sample inferences and confirm output quality matches expectations.

### Step 3 — Update the environment variable and restart the CPU service

Update the `*_BASE_URL` env variable in the CPU node's systemd EnvironmentFile:

```bash
# Edit /etc/voiceos/cpu.env (or equivalent EnvironmentFile path)
# Change:
#   STT_BASE_URL=http://<gpu-host>:8100
# To:
#   STT_BASE_URL=http://<gpu-host>:8101
```

Then restart the voice runtime with the new URL:

```bash
systemctl restart voiceos-voice-runtime
```

Systemd's `TimeoutStopSec=45` (set in Phase 7) gives active calls up to 45 s to drain cleanly before the process exits.

### Step 4 — Monitor for errors

Watch logs for 5 minutes after the restart:

```bash
journalctl -u voiceos-voice-runtime -f
```

Check Prometheus / Grafana for:
- `voiceos_stt_requests_total{status="error"}` — should not spike
- `voiceos_tts_requests_total{status="error"}` — should not spike
- `voiceos_circuit_breaker_state` — should remain `closed` (0)
- `voiceos_stt_latency_ms` — compare percentiles against the performance baseline

### Step 5 — Decommission the old model

Once the new model is stable (≥ 15 minutes, zero error spike):

```bash
# Stop the old model process (it is no longer receiving traffic)
# For vLLM: Ctrl+C or kill the process
# For systemd services: systemctl stop voiceos-stt-old
```

---

## 3. Rollback

If the new model produces errors or high latency, roll back immediately:

```bash
# Revert the env var to the old port
# STT_BASE_URL=http://<gpu-host>:8100

systemctl restart voiceos-voice-runtime
```

Full rollback completes in under 60 seconds (process drain + restart).

---

## 4. VRAM budget

| Service | Current model          | VRAM (measured) |
|---------|------------------------|-----------------|
| STT     | whisper-large-v3-turbo | 6,144 MB        |
| LLM     | Qwen2.5-7B-FP8         | 16,384 MB       |
| TTS     | Veena 3B BF16          | 7,974 MB        |
| **Total** |                      | **30,502 MB**   |

GPU node has 49,140 MB (RTX A6000). Running one extra model during the swap requires < 50% additional VRAM for most single-service swaps.

---

## 5. Secret rotation

If `GPU_SHARED_SECRET` must change at the same time as the model swap:

1. Update Vault KV: `vault kv put voiceos/gpu/shared-secret value=<new-secret>`
2. Update the GPU services' `GPU_SHARED_SECRET` env var
3. Restart GPU services first (they now accept both old and new secret via a 60-second grace window — implement in GPU service if not already done)
4. Restart the CPU voice runtime (it now sends the new secret)
5. After 60 seconds, remove the grace window from GPU services

---

*Last updated: 2026-09-17 — Phase 9f*
