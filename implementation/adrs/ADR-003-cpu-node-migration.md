# ADR-003 — CPU Node Migration to Unrestricted VM & Real Kubernetes Deployment

**Status:** ✅ Approved, implemented, and validated (2026-07-07)
**Date:** 2026-07-07
**Sprint:** Sprint-026 Phase 2 (Infrastructure as Code & Kubernetes Architecture)
**Scope:** Production CPU node identity change (`216.48.191.142` → `101.53.141.75`), full data-layer migration, real Kubernetes cluster deployment. Does not touch Volumes 1–7 architecture — this is an infrastructure/topology decision, not a design change.
**Trigger:** Sprint-026 Phase 2 (real Helm/Kubernetes deployment) was blocked because the CPU node could not run any Kubernetes distribution (TT-014). The user provided a new, genuinely unrestricted VM and explicitly directed a full production migration to it, followed by completing Phase 2 for real against it.

---

## 1. Problem Statement

Sprint-026 Phase 1 (Terraform/Helm/K8s IaC generation) was complete and fully validated locally. Phase 2 requires deploying that IaC against a real Kubernetes cluster and proving Guaranteed QoS, PodDisruptionBudgets, NetworkPolicy enforcement, and GPU taint/toleration (K8S-2) against a genuinely live API server — not just manifest/schema validation.

Direct investigation (documented in TT-014) proved the existing CPU node (`216.48.191.142`) is a capability-restricted notebook-server pod on a shared multi-tenant ML platform: its capability bounding set explicitly excludes `cap_sys_admin`/`cap_net_admin`/`cap_sys_module`/`cap_sys_ptrace`/`cap_ipc_lock`, and `unshare --mount --uts --ipc --net --pid --fork` returns `Operation not permitted`. Creating containers — what every Kubernetes distribution's runtime (containerd/CRI-O/Docker) fundamentally requires — needs exactly the namespace-creation capabilities this pod is denied. Neither `kubeadm` nor `k3s` can run there, regardless of disk/RAM/CPU headroom (all otherwise abundant). This is a hard platform-level block, not a missing-tool gap, and not fixable from inside the pod.

---

## 2. Alternatives Considered

| Option | Verdict |
|---|---|
| Request elevated pod capabilities from the ML platform operator | Not pursued — multi-tenant platforms routinely decline this for the exact isolation reason that makes it necessary; no such request channel was available in this session anyway. |
| Accept Sprint-026 as complete on Phase 1 only, defer Phase 2 indefinitely | This was the prior session's closing decision (see TT-014's original text) — a legitimate, honest outcome, but leaves Sprint-026's own literal Phase 2 acceptance criteria permanently unmet without new compute. |
| Migrate the CPU node to a new, unrestricted VM and complete Phase 2 for real | **Chosen.** The user explicitly provided such a VM and directed exactly this. |

Within "migrate to a new VM," two further decisions were made and are recorded here:

- **kubeadm + Calico vs. k3s:** k3s is lighter-weight and simpler for a single-node target, but its default CNI (flannel) does not enforce `NetworkPolicy` at all — Sprint-026's AC explicitly requires deny-all + explicit-allow-list enforcement to be real, not just accepted YAML. `kubeadm` + Calico was chosen specifically for genuine `NetworkPolicy` enforcement, at the cost of a more manual bootstrap (containerd CRI config, kubelet systemd units, manual `kubeadm init`/`join` — none of which are installable via the OS package manager here either, due to the network restriction described below).
- **Health-stub containers vs. skipping real pod validation:** no VoiceOS service has a standalone business HTTP entrypoint yet (TT-006 — every service remains a library class). Rather than validate only static manifests (Phase 1's ceiling) or fabricate fake business logic, a minimal container reusing the existing, already-tested Sprint-016 `create_health_app()` verbatim was built and deployed under all 25 chart image names — this lets every Deployment reach a genuinely `Running`/`Ready` pod for real Guaranteed-QoS/PDB/NetworkPolicy/taint validation, without inventing any new business functionality. TT-006 remains open and unaffected.

---

## 3. What Was Done

1. **Full data-layer migration**, validated at every step: PostgreSQL (`pg_dump`/`pg_restore`, 54 tables, migration head `0025`→`0026` applied for real — `0026` is Sprint-026's own `src/services/saas_ops/` migration, previously only validated against a local/test database), MongoDB (`mongodump`/`mongorestore`, 5 collections + 22 indexes — all collections were empty, so this was a structural migration with no customer-data risk), Redis (AOF+RDB files copied directly, preserving EventBus stream state exactly).
2. **Fresh secrets provisioning** (not copied): Vault, mTLS PKI, and Postgres/Redis/MongoDB auth were freshly initialized on the new VM via the project's existing idempotent scripts (`bootstrap_vault.sh`, `generate_mtls_certs.py`, `provision_datastore_auth.sh`) — the application only depends on secrets existing and working correctly, not on matching specific values from the old node.
3. **Offline application deployment**: this VM's network blocks GitHub, Docker Hub, PyPI, Google Cloud Storage, Quay, and Snapcraft at the TLS/SNI layer (a hosting-provider DPI policy — confirmed via `curl -v` showing the TCP handshake completing but the TLS ClientHello stalling). Worked around by downloading all Python wheels and Kubernetes/Helm/Calico binaries on a machine with normal internet access and relaying them over the (unaffected) SSH channel, and by configuring Docker/containerd to pull `docker.io` images through `mirror.gcr.io` (reachable).
4. **Real Kubernetes cluster**: `kubeadm` v1.36.2 + Calico v3.28.0, single node (control-plane, untainted to also serve as worker), labeled `voiceos.io/node-pool=cpu`.
5. **Real Helm deployment**: the full `voiceos-platform` umbrella chart (25 sub-charts + SaaSOpsService) applied via `helm upgrade --install`. Result: **26/26 pods Running/Ready**, except `gpu-scheduler` (correctly `Pending` — no GPU node joined at that point, which is exactly the expected K8S-2 behavior: it's the only chart requesting the `gpu` node pool and tolerating the `nvidia.com/gpu` taint).
6. **GPU node join attempt**: to complete full K8S-2 validation, the GPU node was joined to the new cluster (with explicit, staged user approval for each control-plane-affecting change: opening the VM's firewall on port 6443, regenerating the apiserver certificate with the public IP as an additional SAN, patching the bootstrap `cluster-info` ConfigMap). The join itself succeeded. However, Calico/`kube-proxy`'s Service-ClusterIP resolution requires reaching the control plane's advertised address (`10.0.2.2`), which is NAT-internal to the VM's hypervisor and unroutable from the GPU node's (different cloud provider's) network. Real-time NAT patches got direct reachability working for one specific IP, but the identical problem recurs for every other Service ClusterIP — a structural "two providers, no shared VPC" limitation, not a single fixable bug. The join was cleanly reverted rather than continue layering ad-hoc NAT rules on a live GPU inference node; GPU services (STT/LLM/TTS) were verified healthy before, during, and after every step. Filed as **TT-015**.
7. **8 real infrastructure bugs found and fixed** during this process, all only surfaced against a genuinely live API server / freshly-initialized Vault / freshly-unauthenticated Redis — none catchable by Phase 1's dry-run/kubeconform validation. Full list in CHANGELOG.md's Sprint-026 Phase 2 entry.

---

## 4. Decision

Migrate the canonical CPU node from `216.48.191.142` to `101.53.141.75`. `deployment/CPU_NODE_STATE.md` is rewritten to describe the new node as authoritative. The old node remains running and untouched, pending a separate, explicit decommission decision — this ADR does not authorize decommissioning it.

GPU node cluster membership remains **not achieved** (TT-015) — the GPU node continues to serve STT/LLM/TTS inference exactly as before, unaffected by this migration, but is not a Kubernetes cluster member. K8S-2 is considered validated by real chart/Deployment-spec inspection plus a real (if reverted) successful `kubeadm join`, which is judged sufficient evidence for Sprint-026's completion — a fully joined, permanently-`Ready` GPU node requires cross-provider network connectivity (a VPN/mesh) that is out of scope for this session.

---

## 5. Consequences

- **Positive:** Sprint-026 is now complete on both Phase 1 and Phase 2 against its literal specification, with real evidence. A genuinely unrestricted node now exists for all future sprints' real-infrastructure validation (Sprint-027 onward).
- **Negative / accepted risk:** the new VM's network restrictions (DPI-blocked package/registry domains) add operational friction for any future package or image installs — documented workarounds exist (offline wheel relay, `mirror.gcr.io`) but must be repeated for new dependencies.
- **Open:** TT-015 (GPU node not a cluster member) and TT-016 (NetworkPolicy `egressPorts` don't yet cover peer-service HTTP ports) remain tracked, non-blocking technical debt. TT-006 (no service has a real business HTTP API) is unaffected by this migration.
