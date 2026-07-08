# Runbook: Full Region / Full Node Failure (Multi-Region DR)

**RTO target:** ≤ 30 min (Sprint-027.md).
**Scope note:** VoiceOS v2's current production topology is a **single CPU node** (`101.53.141.75`) plus a **single GPU node** (`217.18.55.96`) on two different cloud providers with no shared VPC (TT-015) — there is no second region or standby cluster today. This runbook documents the procedure that would apply once a genuine multi-region/multi-cluster topology exists (Terraform's `staging`/`production` environments target this), and the single-node equivalent that applies right now (full node rebuild from the documented state + machine-rebuild scripts, per `implementation/ROADMAP.md`'s Infrastructure Snapshot & Disaster Recovery section).

## Detection

1. `NodeDown` alert fires for every target on the CPU node simultaneously (not a single service — the whole node/region is unreachable).
2. Confirm the node itself is down, not just a network blip:
   ```bash
   ssh -i ~/.ssh/vm-node-key root@101.53.141.75 "echo alive"
   ```
   A connection timeout (not "connection refused") for several minutes confirms a real outage rather than a transient SSH hiccup.

## Procedure — target multi-region topology (once provisioned)

1. DNS/traffic manager fails over to the standby region's ingress.
2. The standby region's Postgres/Redis/MongoDB replicas (cross-region async replication, `infra/terraform/modules/{database,redis,mongodb}`) are promoted to primary.
3. `voiceos-platform` Helm release in the standby region's cluster is already running (warm standby, not cold) — no additional deploy step, only the data-layer promotion above.
4. Verify: full regression suite + walking-skeleton e2e test against the newly-primary region.

## Procedure — current single-node topology (full node rebuild)

1. Provision a fresh VM matching `CPU_NODE_STATE.md` §1's specs (or better).
2. Run `deployment/cpu/bootstrap.sh` (OS packages, Python, Docker, kubeadm/Helm — see `CPU_NODE_STATE.md` §5.1 for the from-scratch `kubeadm init` sequence, since this node is simultaneously the app host and the K8s control plane).
3. Restore the data layer from the most recent backups: Postgres (`pg_restore` from the last `pg_basebackup` + WAL replay), Redis (AOF+RDB copy), MongoDB (`mongorestore`), Vault (re-init + re-provision datastore auth — Vault's own data is not itself disaster-recoverable across a full loss unless its storage backend was itself backed up, per `CPU_NODE_STATE.md` §7.5's "Vault backup is not optional" warning).
4. Run `deployment/cpu/restore.sh`: applies migrations, recreates MongoDB indexes, re-provisions mTLS PKI, redeploys the full `voiceos-platform` Helm umbrella chart (including this sprint's `voiceos-ops` monitoring stack).
5. Run `deployment/cpu/healthcheck.sh` — full green.
6. Run the full regression suite + walking-skeleton e2e test.
7. Re-point the GPU node's `GPU_SCHEDULER_REPORT_URL` at the new CPU node's IP if it changed.

## Rollback

Do not decommission the failed node until the rebuilt node has passed the full regression suite and a manual smoke check of at least one real call path. Keep the failed node's disks/snapshots for forensics.

## Post-incident

Record actual RTO in `infra/dr/DR_DRILL_REPORT_<date>.md`. If any manual step above could not be automated, file a BACKLOG.md technical-debt entry (same precedent as TT-004's "full reboot DR gap").
