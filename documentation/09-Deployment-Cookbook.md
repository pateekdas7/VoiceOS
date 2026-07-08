# VoiceOS v2 — Documentation Suite

## Document 9 — Deployment Cookbook

**Type:** Practical deployment handbook (documentation layer over Volumes 1–7)
**Status:** Living reference · **Owner:** Platform/SRE
**Authority:** Volumes 1–7 are immutable + canonical. This cookbook gives **practical, step-oriented procedures** that operationalize the deployment architecture already defined — V7 (operations, deployment, K8s, GPU, DR, runbooks), V3 Ch18/21 (backup/DR, deployment), V6 Ch12 (CI/CD), V1 Ch7 (GPU scheduler). It introduces no new architecture; where a procedure and a volume differ, the volume wins. Citations: `V<n> Ch<c>`.

> **Golden rules (from the architecture):** no manual prod changes — IaC + pipeline only (V7 IaC-3); deploys are drain-aware + reversible (DEP-1); migrations are expand-contract (DM-6); GPU work goes through the scheduler (RI-8); secrets come from the vault, never images/config (V4 Ch7 / AR-19). Every procedure below assumes these.

---

## 1. Local development

**Goal:** run VoiceOS locally with parity to prod behavior (not scale).

1. Clone the monorepo (V6 Ch2). Install toolchain (`pyproject.toml`: ruff, mypy, pytest).
2. Bring up dependencies via **Docker Compose** (§3): Postgres, Redis, event bus, minimal mock/edge for telephony.
3. Provide local config from `config/` (schema-validated, V1 App.D); secrets via a local vault/dev-secrets mechanism (never commit secrets, AR-19).
4. For AI components locally: use a small/quantized model or a remote dev inference endpoint; the GPU scheduler runs in a single-node mode.
5. Run `pytest` (unit + integration, DocSuite-08) before pushing.

**Parity note:** dev mirrors prod via the same IaC modules at smaller size (V7 Ch3) — "works in dev" must mean "works in prod."

## 2. Docker (images)

- Images are **minimal, signed, scanned, SBOM-attested** (V6 Ch12 / V4 Ch20); immutable (V7 Ch1).
- One service per image; non-root, read-only rootfs where possible (V7 Ch5).
- **Never** bake secrets into images (AR-19) — injected at deploy from vault (V4 Ch7).
- Build via the pipeline (reproducible, hash-locked deps, V6 Ch12), not ad hoc.

## 3. Docker Compose (local/dev only)

- Compose runs the dependency set for local dev (§1): Postgres, Redis, event bus, supporting services.
- **Not for production** — production is Kubernetes (V7 Ch5). Compose exists for developer parity only.

## 4. Kubernetes deployment (production)

> Production runs on K8s (V7 Ch5). All via IaC (Terraform + Helm, V7 Ch3).

1. **Provision cluster + node pools** (V7 Ch5): CPU/media pool (Guaranteed QoS, high priority — RI-1, K8S-1), GPU pool (tainted, device-plugin — K8S-2), data + system pools.
2. **Deploy data services** (or connect managed): Postgres (+replicas), Redis cluster, event bus, object storage — encrypted at rest (V4 Ch8).
3. **Deploy runtime services** via Helm: media-gateway, audio-pipeline, conversation-engine, and the GPU services (STT/LLM/TTS) onto the GPU pool via the scheduler (V1 Ch7).
4. **Secrets**: vault-backed (CSI/external-secrets, V4 Ch7) — no plaintext K8s Secrets in Git.
5. **Ingress + autoscaling**: ingress/LB; HPA on leading signals (concurrent calls/queue depth, SCALE-1); cluster autoscaler; GPU autoscaling (V7 Ch6/13).
6. **Verify health** (§12) before serving traffic.

## 5. GPU server deployment

1. GPU nodes join the **GPU pool** (tainted; `nvidia.com/gpu` resource).
2. **Load + warm models** (Veena TTS FP16, Whisper Large-v3 Turbo FP8 STT, Qwen2.5-7B-Instruct-FP8 LLM) — weights from artifact storage (not repo); prime KV/prefix caches (V1 Ch12/13).
3. **Register with the GPU Scheduler** (V1 Ch7): initialize the VRAM ledger; admission control enforces OOM-by-construction (RI-8).
4. **Warm-before-admit** (GPU-1): do **not** route hot-path traffic until models are warm + health-checked.
5. Verify GPU health (utilization, VRAM, temp/ECC — V7 Ch6).

## 6. CPU / media server deployment

1. CPU/media pods land on the **CPU pool** with **Guaranteed QoS + high priority** (RI-1 / K8S-1) — protected from noisy neighbors (no hot-path jitter).
2. Anti-affinity to spread across nodes/AZs (HA).
3. Verify no blocking on the media path (RI-1) and that buffers are bounded (RI-3).

## 7. Data services

### Redis (hot state)
- Cluster mode + replicas (V3 Ch4/13); TTLs for non-authoritative state. **Never authoritative** (DM-1) — must be rehydratable (V3 Ch7).

### Postgres (authoritative)
- Primary + read replicas (V3 Ch5); encrypted at rest (V4 Ch8); connection pooling (V7 Ch16). The authoritative relational store.

### Event log
- Append-only, durable (V3 Ch3); retention per policy (V4 Ch9). The lineage/replay/audit substrate.

> **On "MongoDB":** the canonical authoritative store is **Postgres**; events live in the **event log**; hot state in **Redis** (DocSuite-05 §3). Any document-store usage is non-authoritative and follows DM-1.

## 8. AI runtime deployment

### vLLM (LLM)
- Deploy on GPU pool via the scheduler (V1 Ch7/13); continuous batching + KV/prefix cache; model from the approved catalog (V5 Ch14). Warm before admit (GPU-1).

### STT (Faster-Whisper Large-v3 Turbo FP8)
- GPU pool; warm; via scheduler (V1 Ch8). Fallback configured (V3 Ch13).

### TTS (Veena, 24 kHz WS)
- GPU pool; warm; WebSocket streaming + SOXR (V1 Ch17/20). Fallback voice configured (V3 Ch13).

## 9. Rolling upgrades

> Drain-aware, gated, reversible (DEP-1 / V7 Ch4).

1. Pipeline builds + gates the signed artifact (V6 Ch12).
2. **Canary** (5% traffic) → health + perf gates on live traffic (V3 Ch12/19).
3. **Progressive** (25→50→100%), **draining** old instances (finish in-flight calls, accept no new — **zero dropped calls**).
4. **Migrations** run expand-contract (DM-6), decoupled from code rollout.
5. **AI-config/prompt** versions roll in lockstep (V5 Ch14).
6. **Auto-rollback** armed throughout; post-deploy validation (§12 / CICD-8).

## 10. Rollback

1. Gate failure on canary → **auto-rollback** to the prior signed artifact + prior AI-config/prompt version (< 5 min, CICD-7).
2. Blue-green keeps the prior version warm for instant fallback (V7 Ch4).
3. Migrations stay forward-compatible (expand-contract) so rollback is safe (old code works against expanded schema).
4. Confirm SLOs restored (§12). File a post-mortem if it was a real regression (V7 Ch11).

## 11. Backup, restore & warmup

### Backup (V3 Ch18)
- Continuous backups + replication (≤ 5 min RPO); **validate restores continuously** (DR-2) — an unvalidated backup is assumed broken.

### Restore (V3 Ch18 / V7 Ch14)
- Point-in-time restore to the RPO boundary; verify integrity (V4 Ch11 hash chain); reconcile the event log (deterministic replay, V3 Ch7) so no committed effect is lost/duplicated.

### Warmup (V1 Ch7/12/13 / V7 Ch6)
- Pre-load + warm models; prime KV/prefix caches; run synthetic calls (§12) so first real call is in budget (no cold-start, GPU-1).

## 12. Health verification (before serving / after deploy)

Run **before** routing traffic and **after** every deploy (CICD-8):
1. **Liveness/readiness** green for all pods (V3 Ch12).
2. **Synthetic canary calls** through the full path — verify behavior + **first-audio p95 ≤ 1.5 s** (V1 Ch23).
3. **SLO checks**: availability, error rate, **0 duplicate effects** (V3 Ch8), **audit completeness** (V4 Ch11).
4. **GPU health**: warm models, VRAM ledger consistent, 0 OOM (RI-8).
5. **Observability**: metrics/logs/traces flowing (V7 Ch7/9/10).
Only declare the deploy validated when these pass on real/synthetic traffic.

## 13. Scaling

- **Up/out:** HPA on leading signals (SCALE-1); cluster autoscaler for nodes; GPU autoscaling with warm-before-admit (V7 Ch6/13); predictive/scheduled pre-scale for known peaks (V7 Ch12).
- **Down/in:** drain-aware (SCALE-2) — finish in-flight calls; respect min-warm baseline (no cold hot path).
- **Capacity:** maintain headroom ≥ spike/failover (CAP-1, V7 Ch12).
- **Global:** add regions for latency/residency/capacity; route within residency (GLOBAL-1, V7 Ch17).

## 14. Troubleshooting (pointers)

Use the **production runbooks** (V7 Ch19) + **debugging playbooks** (V6 Ch15). Start from the trace + `DecisionEnvelope` lineage (DBG-1). Quick map:

| Symptom | Runbook | First move |
|---|---|---|
| High latency | RB-LAT (V7 Ch19) | trace → regressed budget line (V6 DBG-P1) |
| GPU errors / OOM | RB-GPU | drain/reschedule; OOM = RI-8 P-sev |
| Redis issues | RB-REDIS | failover + rehydrate (non-authoritative) |
| DB issues | RB-DB | failover/PITR; check tenant predicate (AR-8) |
| STT/LLM/TTS degraded | RB-AI | engage fallback (V3 Ch13); rollback if deploy-correlated |
| Queue backlog | RB-QUEUE | scale consumers; shed lowest-priority |
| Memory leak | RB-MEM | rolling-restart; find unbounded structure (RI-3) |

## 15. Emergency procedures

> Use the **Emergency Patch** (V6 PLAY-7) + **Incident Management** (V7 Ch11) processes. Mitigate first (INC-1).

1. **Declare incident** + assign IC (SEV1/2, V7 Ch11). Open the relevant runbook (§14).
2. **Mitigate first:** rollback (§10), regional failover (V7 Ch14, RTO ≤ 30 min), engage fallback (V3 Ch13), or scale (§13) — restore service before deep diagnosis.
3. **Emergency patch (if needed):** hotfix from the released tag; **gates still run** (expedited, never skipped, GIT-6); deploy via pipeline; forward-port.
4. **Communicate:** internal + customer/status page; regulatory if required (V4 Ch16).
5. **Verify** service restored (§12); **post-mortem** (blameless, V7 Ch11 / V6 Ch20); track actions to closure.
6. **Security incident:** also invoke V4 Ch17 (containment, forensics, breach notification).

**Never** in an emergency: bypass safety/authority controls (Law of Authority, Output Validation), disable gates to "ship the fix," or make manual prod changes outside the pipeline — these convert one incident into two.

---

## 16. Deployment checklist (quick)

```text
[ ] Signed/scanned artifact + SBOM (V6 Ch12)   [ ] Strategy by risk (V7 Ch4)
[ ] Drain-aware (DEP-1) — 0 dropped calls       [ ] Health+perf gates + auto-rollback armed
[ ] Secrets from vault (V4 Ch7), none in image  [ ] Migrations expand-contract (DM-6)
[ ] AI-config/prompt versions pinned (V5 Ch14)  [ ] Observability live for new version
[ ] Rollback path verified BEFORE promote       [ ] Post-deploy validation (§12 / CICD-8)
```
*(Full checklists: V7 Appendices + DocSuite-12.)*

---

## Version history & change log

| Version | Date | Change | Owner |
|---|---|---|---|
| 1.0 | (initial) | Deployment cookbook consolidated from V7 / V3 / V6 Ch12 | Platform/SRE / Documentation |

**Change-log policy:** procedures track the architecture (V7) — any change in V7 deployment/operations is reflected here; this cookbook never overrides V7. The suite consistency audit (DocSuite-12) verifies operational workflows are documented.

*End of Document 9 — Deployment Cookbook.*
