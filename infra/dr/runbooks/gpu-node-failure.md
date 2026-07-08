# Runbook: GPU Node Failure / Replacement

**RTO target:** ≤ 30 min for full replacement; sub-second for in-fleet reroute (GPU-2 invariant, Sprint-008's graceful-failover guarantee).

## Scope

The current GPU node (`217.18.55.96`, see `deployment/GPU_NODE_STATE.md`) hosts all three inference services (STT/LLM/TTS) as a single physical node — it is **not yet a joined Kubernetes cluster member** (TT-015: cross-provider NAT blocks it). This means:

- **In-fleet reroute** (GPU Scheduler draining to a surviving GPU node, Sprint-008's `GPUFailureStrategy`) requires a second GPU node to exist and be registered with the GPU Scheduler's VRAM ledger. Today's fleet is a single node — this runbook documents the mechanism (validated against `monitoring/gpu-fleet/fleet_health.py`'s simulated multi-node fixture) but a real second GPU node is not currently provisioned.
- **Full node replacement** (this node dies entirely) requires re-running the GPU bootstrap/restore procedure on new hardware.

## Detection

1. Prometheus `GPUUnavailable` alert fires (`up{job="gpu-node"} == 0`), or `GPUFleetDegraded`/`GPUFleetSevereDegradation` fires (`monitoring/gpu-fleet/fleet_health.py`'s `fleet_health_score()` dropping below 0.8/0.5).
2. Confirm via SSH (ask the user for GPU-node access approval first — standing rule, `GPU_NODE_STATE.md` header):
   ```bash
   ssh -i ~/.ssh/temporary.pem ubuntu@<gpu-node-ip> "nvidia-smi && systemctl is-active voiceos-stt voiceos-llm voiceos-tts"
   ```

## Procedure — in-fleet reroute (multi-node fleet, once a second node exists)

1. `GPUFleetHealthMonitor.fleet_health_score()` drops below 0.8 as the failing node's health probe starts failing (`monitoring/gpu-fleet/fleet_health.py`).
2. The GPU Scheduler's `GPUFailureStrategy` (Sprint-008/015) drains new admissions away from the failing node — in-flight calls on it degrade gracefully (TTS fade + re-queue, per the documented crash-recovery matrix, Sprint-015).
3. `ModelWarmupOrchestrator` (`monitoring/gpu-fleet/warmup.py`) ensures the surviving node(s) already have all three model pools warm — no cold-start penalty during the reroute (GPU-1 invariant, applied at fleet level).
4. Verify: `fleet_health_score()` recovers toward 1.0 as the failing node is fenced out of the ledger.

## Procedure — full node replacement (current single-node fleet)

1. Provision a fresh GPU instance (same class as documented in `GPU_NODE_STATE.md` §1–3 — NVIDIA L4, ≥23 GB VRAM).
2. Run the GPU bootstrap procedure (`GPU_NODE_STATE.md` §17): NVIDIA driver/CUDA/cuDNN/Container Toolkit, Python 3.12 venv, vLLM/faster-whisper/torch stack.
3. Run `deployment/gpu/restore.sh`: downloads models per `deployment/gpu/model_manifest.yaml`, starts services in order (LLM → STT → TTS per `GPU_NODE_STATE.md` §13), runs `deployment/gpu/validate_latency.py`.
4. Re-point the CPU node's GPU Scheduler at the new node's IP (env var, not hardcoded — `GPU_SCHEDULER_REPORT_URL` reversed direction, or the scheduler's own target config).
5. Verify: `deployment/gpu/healthcheck.sh` all green; `bash infra/dr/scripts/verify-recovery.sh --component gpu`.

## Rollback

Keep the failed node's systemd unit files/logs for post-mortem; do not wipe until root cause is understood (same "investigate, don't discard" principle as the rest of this project's incident handling).

## Post-incident

Record actual recovery time in `infra/dr/DR_DRILL_REPORT_<date>.md`. If this was a full replacement, update `GPU_NODE_STATE.md`'s node-identity fields (new UUID, new access IP).
