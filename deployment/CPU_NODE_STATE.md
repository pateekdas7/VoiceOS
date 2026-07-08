# CPU Node State — VoiceOS v2

> **Living document.** Updated after every sprint. Always describes the **complete current state** of the CPU node — not just the latest changes. Use this document to recreate the CPU node from scratch on any fresh server.

**Last updated:** Sprint-027 (2026-07-08) — The full production observability stack (Prometheus/Grafana/Alertmanager/Loki/FluentBit/OTel Collector/Jaeger) is now deployed for real into the `voiceos-ops` namespace, alongside `CostOptimizerService`/`OpsAnalyticsService` (library classes, TT-006 unchanged). All 26 VoiceOS service pods (25 charts + SaaSOpsService) show `up` in Prometheus; 15 Grafana dashboards (SLO/call-funnel/GPU-fleet/latency/reliability/business + 5 governance + 4 security) provisioned and verified; Loki log search by `call_id` verified against a real StructuredLogger JSON line; a real OTLP trace verified end-to-end in Jaeger; Alertmanager routing verified (both a synthetic burn-rate alert and real `NodeDown`/`GPUUnavailable` alerts routed correctly to `pagerduty-critical`). A real Postgres failover DR drill completed in 4s (target ≤60s) with zero data loss (full regression suite unchanged at 2091 passed/1 skipped before and after) — see `infra/dr/DR_DRILL_REPORT_2026-07-08.md`. 3 real infrastructure bugs were found and fixed during this deployment (see §13 and CHANGELOG.md's Sprint-027 entry) — most notably that this cluster's `containerd` CRI log format is plain-text (not Docker's json-file format FluentBit's default `docker` parser expects), and that the same apiserver-NAT limitation documented in TT-015 also blocks in-cluster pods (not just the GPU node) from reaching `kubernetes.default.svc` via its ClusterIP, which was silently starving FluentBit's own Loki delivery until the `kubernetes` filter was removed. See §13 below for full observability endpoint/dashboard/alert-routing inventory. Previously: Sprint-026 Phase 2 (2026-07-07) — **This node replaces the previous CPU node (`216.48.191.142`) entirely.** The previous node was proven incapable of running any Kubernetes distribution (TT-014: capability-restricted notebook-server pod on a shared multi-tenant ML platform — `unshare` denied, `cap_sys_admin`/`cap_net_admin` absent). Per explicit user direction, a full production migration was performed to this genuinely unrestricted VM: complete data-layer migration (PostgreSQL 54 tables + migration `0026` applied for real, MongoDB 5 collections/22 indexes, Redis AOF+RDB — zero customer-data loss, MongoDB collections were already empty), fresh Vault/mTLS-PKI/datastore-auth provisioning (not copied — the application only depends on secrets existing and working, not on specific values), and a real `kubeadm` v1.36.2 + Calico v3.28.0 Kubernetes cluster with the full `voiceos-platform` Helm umbrella chart deployed (26/26 pods Running/Ready except `gpu-scheduler`, correctly `Pending` — see §5). 8 real infrastructure bugs were found and fixed during this migration (see CHANGELOG.md's Sprint-026 Phase 2 entry and `implementation/BACKLOG.md`'s TT-014/TT-015/TT-016). The old node (`216.48.191.142`) remains running and untouched pending a separate, explicit decommission decision — it is **no longer the canonical CPU node** and should not be used for new work.
**Node address:** `101.53.141.75`
**Access key:** `~/.ssh/vm-node-key` (ED25519)
**Access user:** `root`
**Environment:** Real KVM virtual machine (KubeVirt-backed, hardware vendor "KubeVirt"), genuinely unrestricted — confirmed via `unshare --mount --uts --ipc --net --pid --fork` (succeeds), full capability bounding set (`cap_sys_admin`/`cap_net_admin`/`cap_sys_module`/`cap_sys_ptrace`/`cap_ipc_lock` all present), real `systemd` init (not SysV, not a K8s pod). Own kernel (6.8.0-117-generic), own `/proc/1/cgroup` (`0::/init.scope` — not inside another container).

> **Network note (important — affects any future package/image installs):** this VM's outbound network blocks GitHub, Docker Hub/`registry-1.docker.io`, Quay, PyPI (`pypi.org`/`files.pythonhosted.org`), Google Cloud Storage, and Snapcraft at the TLS/SNI layer (a hosting-provider DPI policy — TCP connects fine, but the TLS ClientHello stalls and times out; confirmed via `curl -v`). `archive.ubuntu.com`, `repo.mongodb.org`/`pgp.mongodb.com`, `download.docker.com`, and `registry.k8s.io` all work normally. **Workarounds already in place:**
> - Docker/containerd registry pulls from `docker.io` are routed through `mirror.gcr.io` (reachable) via `/etc/docker/daemon.json`'s `registry-mirrors` and `/etc/containerd/certs.d/docker.io/hosts.toml`.
> - Python packages must be installed offline: download wheels on a machine with normal internet (`pip download --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 ... --python-version 312 --implementation cp --abi cp312 --only-binary=:all:`), transfer via `scp`, then `pip install --no-index --find-links=<dir>`.
> - Any future `kubectl`/`kubeadm`/`helm`/binary installs referencing `dl.k8s.io`, `get.helm.sh`, or GitHub releases must be downloaded elsewhere and relayed via `scp`.

---

## 0. Environment Notes

This is a real, standalone VM — not a Kubernetes pod, not a shared multi-tenant notebook platform. It is simultaneously:
- The **application host**: Postgres/Redis/MongoDB/Vault run as native processes (systemd-managed where applicable), and the VoiceOS application code + Python venv live here.
- The **Kubernetes control-plane node**: a real `kubeadm` cluster is initialized here, and — since this is a single-node cluster — this same node is untainted and also runs as the sole worker, hosting the full `voiceos-platform` Helm-deployed application tier as real pods.

---

## 1. Operating System

| Property | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS (Noble Numbat) |
| Kernel | 6.8.0-117-generic |
| Architecture | x86_64 |
| Hostname | `n-4051343f-7192-48cf-a1e8-2fc23b35c9ec-0` |
| User | root |
| Init system | systemd (real — `systemd 255`) |
| CPU cores | 8 |
| Memory | 12 GiB total |
| Disk | 84 GB (`/dev/vda1`), ~68 GB free after full deployment |

---

## 2. Required System Packages

Installed via `deployment/cpu/bootstrap.sh` (same script as before — the `IN_KUBE_POD` auto-detection now correctly evaluates `false` on this node, so Docker/Helm install for real):

```bash
apt-get install -y \
  curl wget git unzip jq \
  build-essential pkg-config \
  ca-certificates gnupg lsb-release \
  net-tools nmap tcpdump \
  htop iotop sysstat \
  supervisor \
  libssl-dev libffi-dev \
  libpq-dev \
  apt-transport-https software-properties-common
```

---

## 3. Python

| Property | Value |
|---|---|
| Version | Python 3.12.3 (Ubuntu 24.04 default — no deadsnakes PPA needed, unlike the old node) |
| Location | `/usr/bin/python3.12` |
| Virtual env | `/opt/voiceos/venv` (requires `python3.12-venv` package, not present by default on 24.04 — install explicitly) |
| Packages | Installed **offline** via relayed wheels (see the network note in the header) — `pip install --no-index --find-links=<wheel-dir> --no-build-isolation -e .` |

```bash
apt-get install -y python3.12-venv python3.12-dev
python3.12 -m venv /opt/voiceos/venv
/opt/voiceos/venv/bin/pip install --no-index --find-links=<wheel-dir> --upgrade pip setuptools wheel
cd /opt/voiceos/app && /opt/voiceos/venv/bin/pip install --no-index --find-links=<wheel-dir> --no-build-isolation -e .
```

---

## 4. Docker

| Property | Value |
|---|---|
| Docker Engine | 29.6.1 |
| Status | Active, enabled |
| Registry mirror | `docker.io` pulls routed through `mirror.gcr.io` (`/etc/docker/daemon.json`: `{"registry-mirrors": ["https://mirror.gcr.io"]}`) — required due to the DPI block on `registry-1.docker.io` |

```bash
curl -fsSL https://get.docker.com | sh
cat <<EOF > /etc/docker/daemon.json
{"registry-mirrors": ["https://mirror.gcr.io"]}
EOF
systemctl restart docker
```

---

## 5. Kubernetes

**A real, working cluster — deployed and fully validated in Sprint-026 Phase 2.**

| Component | Value |
|---|---|
| Distribution | `kubeadm` v1.36.2 (chosen over k3s specifically because Calico gives genuine `NetworkPolicy` enforcement — k3s's default flannel CNI does not enforce `NetworkPolicy` at all, and Sprint-026's AC requires real enforcement, not just accepted YAML) |
| CNI | Calico v3.28.0 |
| Container runtime | containerd (the same one installed as a Docker dependency, configured for CRI use via `/etc/containerd/config.toml`'s `SystemdCgroup = true` and `config_path = '/etc/containerd/certs.d'`) |
| Cluster shape | Single node, both control-plane and worker (untainted after init) |
| Advertise address | `10.0.2.2` (this VM's NAT-internal address; the apiserver certificate also includes `101.53.141.75` [public IP] and `10.0.2.2` as SANs — added specifically to let the GPU node's `kubeadm join` reach it, see §5.4 below) |
| kubeconfig | `/etc/kubernetes/admin.conf` (`export KUBECONFIG=/etc/kubernetes/admin.conf`) |
| Node label | `voiceos.io/node-pool=cpu` (required — every chart's Deployment uses `nodeSelector: voiceos.io/node-pool: <cpu|gpu>`, and this must be applied manually after `kubeadm init`, it is not automatic) |

### 5.1 Bootstrap procedure (from a fresh VM with full capabilities)

```bash
# Prerequisites
swapoff -a  # (usually already off on a fresh cloud VM)
cat <<EOF > /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
modprobe overlay && modprobe br_netfilter
cat <<EOF > /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system

# containerd CRI config (reuses the Docker-installed containerd)
mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
sed -i "s/SystemdCgroup = false/SystemdCgroup = true/" /etc/containerd/config.toml
sed -i "s|config_path = .*|config_path = '/etc/containerd/certs.d'|" /etc/containerd/config.toml
mkdir -p /etc/containerd/certs.d/docker.io
cat <<EOF > /etc/containerd/certs.d/docker.io/hosts.toml
server = "https://registry-1.docker.io"
[host."https://mirror.gcr.io"]
  capabilities = ["pull", "resolve"]
EOF
systemctl restart containerd

# kubeadm/kubelet/kubectl binaries — download elsewhere (dl.k8s.io is blocked
# here, see the network note) and relay via scp, then:
install -m 0755 kubeadm kubelet kubectl /usr/local/bin/

# kubelet systemd unit + kubeadm drop-in (standard, not installed via apt
# since the .deb packages aren't reachable here either)
# — see the full unit file content in a prior session's transcript, or
#   reconstruct from any standard kubeadm/kubelet .deb package's unit files.
systemctl enable kubelet

# Init
kubeadm init --pod-network-cidr=192.168.0.0/16 \
  --apiserver-advertise-address=10.0.2.2 \
  --cri-socket=unix:///run/containerd/containerd.sock

export KUBECONFIG=/etc/kubernetes/admin.conf
mkdir -p ~/.kube && cp /etc/kubernetes/admin.conf ~/.kube/config

# Calico (manifest downloaded elsewhere, relayed via scp — raw.githubusercontent.com is blocked here)
kubectl apply -f calico.yaml
# Pre-pull the 3 Calico images via the mirror.gcr.io route if they don't
# pull automatically on first daemonset schedule:
crictl pull docker.io/calico/cni:v3.28.0
crictl pull docker.io/calico/node:v3.28.0
crictl pull docker.io/calico/kube-controllers:v3.28.0

# Untaint (single-node cluster — this node must also run workloads)
kubectl taint nodes --all node-role.kubernetes.io/control-plane-

# Label for the cpu node pool (every chart's nodeSelector requires this)
kubectl label node $(hostname) voiceos.io/node-pool=cpu
```

### 5.2 Cluster configuration and application deployment

```bash
cd /opt/voiceos/app
export KUBECONFIG=/etc/kubernetes/admin.conf

# infra/k8s/ — namespaces, priority classes, resource quotas, deny-all NetworkPolicy
kubectl apply -f infra/k8s/namespaces.yaml
kubectl apply -f infra/k8s/priority-classes.yaml
kubectl apply -f infra/k8s/resource-quotas.yaml
kubectl apply -f infra/k8s/cluster-policies/default-deny.yaml

# Helm umbrella chart (all 25 service sub-charts + SaaSOpsService)
helm dependency update infra/helm/voiceos-platform/
helm upgrade --install voiceos-platform infra/helm/voiceos-platform/ \
  -f infra/helm/values/dev-values.yaml --wait --timeout 5m
```

**Container image note:** every chart's `values.yaml` references an image like `voiceos/<service>:latest` with `pullPolicy: IfNotPresent`. No VoiceOS service has a real business-logic HTTP entrypoint yet (TT-006 — every service is still a library class). For real infrastructure validation (Guaranteed QoS/PDB/NetworkPolicy/taint against genuinely `Running` pods), a minimal shared image is used: `deployment/k8s/health_stub/` builds a container that runs the existing, already-tested Sprint-016 `src/libs/health.create_health_app()` (no new business logic) as a real process. Build it once and tag it under all 25 chart image names:

```bash
docker build -t voiceos/health-stub:latest -f deployment/k8s/health_stub/Dockerfile .
for svc in admin-portal ai-config ai-governance analytics api-platform audio-preprocessing \
  auth authz billing campaign-management collections contact-center conversation-engine crm \
  dialogue-manager gpu-scheduler integration-platform llm-runtime media-gateway metering \
  policy-engine stt tenant-management tts vad-endpointing saas-ops; do
  docker tag voiceos/health-stub:latest "voiceos/${svc}:latest"
  docker save "voiceos/${svc}:latest" | ctr -n k8s.io images import -
done
```

When real service HTTP entrypoints exist (a future sprint, closing TT-006), replace this with real per-service images.

### 5.3 Real Kubernetes validation (Sprint-026 Phase 2, evidenced)

```bash
# Guaranteed QoS — all 27 pods
kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.status.qosClass}{"\n"}{end}' | grep voiceos-platform- | sort | uniq -c
# → 27 Guaranteed

# PDBs — 26 present, minAvailable: 1
kubectl get pdb --all-namespaces | grep voiceos

# NetworkPolicy — deny-all in all 4 namespaces + 25 explicit allow-lists
kubectl get networkpolicies --all-namespaces | grep voiceos

# GPU taint/toleration (K8S-2) — only gpu-scheduler tolerates it, correctly
# stays Pending with no GPU node joined (see GPU_NODE_STATE.md §7 and
# BACKLOG.md's TT-015 for why a real joined GPU node isn't available yet)
kubectl get pods -n voiceos-runtime -l app.kubernetes.io/name=gpu-scheduler
```

NetworkPolicy enforcement was proven with a real cross-pod test, not just presence: an undeclared cross-namespace HTTP call (`voiceos-runtime` → `voiceos-platform`, no `allowedNamespaces` entry) times out; DNS (the always-allowed baseline egress) resolves correctly from every pod. See `implementation/BACKLOG.md`'s **TT-016** for a real, found-but-not-fixed gap: chart-generated `egressPorts` only ever cover datastore ports, never peer-service HTTP ports — once real business-logic APIs exist, service-to-service NetworkPolicy egress rules will need to be added.

### 5.4 GPU node — not a cluster member (TT-015)

The GPU node (`217.18.55.96`, see `GPU_NODE_STATE.md`) was joined to this cluster via `kubeadm join` during Sprint-026 Phase 2 validation — the join itself succeeded (after opening this VM's firewall on port 6443 and adding it as an apiserver cert SAN, both applied and still in effect). However, Calico/`kube-proxy`'s Service-ClusterIP resolution requires reaching this node's advertised address (`10.0.2.2`), which is NAT-internal and unroutable from the GPU node's (different cloud provider's) network — a structural "no shared VPC" limitation, not a single fixable bug. The join was cleanly reverted (`kubeadm reset` on the GPU node, `kubectl delete node` here) rather than leave ad-hoc NAT rules on a live GPU inference node. **If re-attempting:** real network connectivity (VPN/mesh) between the two providers is needed first — see BACKLOG.md's TT-015 for the full evidence trail, including exactly which iptables/route patches were tried and why they don't fully resolve the issue.

---

## 6. Helm

| Property | Value |
|---|---|
| Version | v3.16.3 |
| Status | Installed, deployed the full `voiceos-platform` umbrella chart for real (see §5.2) |

```bash
helm version
helm list --all-namespaces
helm history voiceos-platform
```

---

## 7. Infrastructure Services

### 7.1 Redis

| Property | Value |
|---|---|
| Version | redis-server (Ubuntu 24.04 default) |
| Deployment | Native process, systemd-managed (`systemctl restart redis-server`) |
| Port | 6379 |
| Auth | ✅ Enforced — `requirepass` set via `scripts/vault/provision_datastore_auth.sh`, value in Vault `secret/voiceos/redis` |
| Persistence | AOF + `volatile-ttl` eviction, hardened via `bootstrap.sh` (same hardening baked in from the start, per the TT-002 precedent) |
| Data | Migrated from the old node — `appendonly.aof` + `dump.rdb` copied directly (preserves EventBus stream state, not regenerated) |

### 7.2 PostgreSQL

| Property | Value |
|---|---|
| Version | PostgreSQL 16.14 (Ubuntu 24.04 default repo — **note deviation:** the old node ran 14.23; this node runs 16 since it's what Ubuntu 24.04 provides. `pg_dump`/`pg_restore` across major versions worked cleanly for the migration.) |
| Deployment | Native process, systemd-managed |
| Port | 5432 |
| Database | `voiceos`, owner `voiceos` |
| Migration head | `0026` (applied for real during the migration — the old node was at `0025`; `0026` is Sprint-026's own `src/services/saas_ops/` migration) |
| Tables | 54 (matches the old node's `0025` state, since `0026` is purely additive: `feature_flags`/`tenant_rollout_rings`/`fleet_versions`/`tenant_migrations`) |

```bash
PGPASSWORD=<from-vault> psql -h localhost -U voiceos -d voiceos -c "SELECT 1;"
cd /opt/voiceos/app && POSTGRES_DSN=<from-vault-via-gen_env.py> alembic current   # → 0026 (head)
```

### 7.3 MongoDB

| Property | Value |
|---|---|
| Version | MongoDB 7.0.37 (identical to the old node) |
| Deployment | Native process (`mongod --dbpath /var/lib/mongodb ... --auth`), `security.authorization: enabled` set in `/etc/mongod.conf` |
| Port | 27017 |
| Database | `voiceos` — 5 collections migrated (`response_plans`, `decision_envelopes`, `call_transcripts`, `call_lineage`, `sprint003_connectivity`), all were empty on the old node (0 documents each — this was a structural, not data, migration), 22 indexes recreated via `scripts/db/mongodb/create_indexes.py` (idempotent, safe to re-run) |
| Auth | ✅ Enforced — scoped `voiceos` user, `readWrite` on `voiceos` db only |

### 7.4 Event Bus (Redis Streams)

Unchanged in design from the old node (`src/libs/event_bus/`, stream `voiceos-events`, consumer group `main-group`, DLQ `dlq:voiceos-events`). The stream itself had 0 pending events at migration time (`scripts/eventbus_recovery.py` self-healed the consumer group on first run against the migrated AOF data, as designed).

### 7.5 Vault

| Property | Value |
|---|---|
| Version | HashiCorp Vault v2.0.3 (same as the old node) |
| Storage | File backend (`/opt/vault/data`) |
| Provisioning | **Freshly initialized on this node** (not copied from the old node — new unseal key + root token in `/opt/vault/init.json`, new `voiceos-app`-scoped token in `/opt/vault/app_token.json`) via `scripts/vault/bootstrap_vault.sh` |
| Secrets | KV v2 at `secret/`, Transit at `transit/` — Postgres/Redis/MongoDB passwords freshly generated and seeded via `scripts/vault/provision_datastore_auth.sh` |

**Two real bugs found and fixed in `bootstrap_vault.sh` during this migration** (this was the first time the script ever ran against a genuinely fresh, never-initialized Vault — every prior run in this project's history was against an already-initialized instance, so these bugs were latent for the script's entire existence):
1. The health check used `curl -sf`, which treats Vault's correct `501 Not Initialized` response as a failure.
2. The unseal-check (`vault status | grep -q ...`) — `vault status` itself exits 2 when sealed (by CLI design), which under `pipefail` poisons the pipeline's exit code regardless of what `grep` matched.

Both fixed with a `vault_reachable()` helper (checks for a real connection failure, not any non-2xx status) and a capture-then-grep pattern, respectively. See `scripts/vault/bootstrap_vault.sh` and CHANGELOG.md's Sprint-026 Phase 2 entry.

**A third bug found in `provision_datastore_auth.sh`:** Redis auth detection checked the authenticated ping before the unauthenticated one — `redis-cli -a <anything> ping` returns `PONG` even when Redis has no password configured at all (AUTH against a passwordless server is a harmless no-op), producing a false "already configured" positive that would have left Redis completely unauthenticated. Fixed by checking the unambiguous unauthenticated-ping-succeeds signal first.

### 7.6 mTLS PKI

Freshly generated on this node via `scripts/pki/generate_mtls_certs.py --out-dir /opt/voiceos/certs` — CA + 5 service leaf certificates (`ai-governance-service`, `auth-service`, `authz-service`, `conversation-engine`, `policy-engine-service`), matching the old node's cert inventory exactly.

---

## 8. Directory Structure

```
/opt/voiceos/
├── app/                    # Application code (rsync'd from the authoritative dev repo)
├── venv/                   # Python 3.12 virtual environment
├── certs/                  # mTLS PKI (CA + 5 leaf certs)
├── logs/
└── recordings/

/etc/kubernetes/            # kubeadm cluster state (admin.conf, pki/, manifests/)
/etc/containerd/            # containerd CRI config + certs.d/ (docker.io mirror)
/opt/vault/                 # Vault data/config/logs + init.json/app_token.json
```

---

## 9. Environment Variables / Secrets

Same pattern as the old node — no `.env` file with plaintext secrets; connection strings are built at runtime from Vault via `scripts/vault/gen_env.py`:

```bash
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="$(python3 -c "import json; print(json.load(open('/opt/vault/app_token.json'))['auth']['client_token'])")"
cd /opt/voiceos/app && source /opt/voiceos/venv/bin/activate
eval "$(python3 scripts/vault/gen_env.py)"   # exports POSTGRES_DSN/REDIS_URL/MONGODB_URI/PYTHONPATH
```

---

## 10. Health Check Commands

```bash
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="$(python3 -c "import json; print(json.load(open('/opt/vault/app_token.json'))['auth']['client_token'])")"
cd /opt/voiceos/app && source /opt/voiceos/venv/bin/activate
eval "$(python3 scripts/vault/gen_env.py 2>/dev/null)"
export POSTGRES_PASSWORD="$(vault kv get -field=value secret/voiceos/postgres)"
export REDIS_PASSWORD="$(vault kv get -field=value secret/voiceos/redis)"
export MONGO_URI="$MONGODB_URI" REDIS_HOST=localhost POSTGRES_HOST=localhost \
       POSTGRES_USER=voiceos POSTGRES_DB=voiceos MTLS_CA_CERT_PATH=/opt/voiceos/certs/ca.crt
bash deployment/cpu/healthcheck.sh
```

A real bug was found and fixed in `healthcheck.sh` during this migration: the MongoDB auth check had the identical `pipefail`-vs-nonzero-exit bug as Vault's unseal check above (`mongosh --eval ... | grep -q "requires authentication"` — `mongosh` exits non-zero on the expected auth error, poisoning the pipeline regardless of what `grep` matched). Fixed with the same capture-then-grep pattern.

**Expected result:** all infra/data-layer/library checks OK. The 8 application-service HTTP-listener checks (`media-gateway:8080` etc.) FAIL — this is the documented pre-Sprint-026 baseline (TT-006: every service is a library class, no standalone business HTTP API — the health-stub Kubernetes deployment proves the *infrastructure* path works, not business logic reachable from these bare-process checks).

---

## 11. Known Deviations from the Old Node

| Item | Old node (`216.48.191.142`) | This node (`101.53.141.75`) | Impact |
|---|---|---|---|
| OS | Ubuntu 22.04.5 LTS | Ubuntu 24.04.4 LTS | None functionally — newer LTS |
| PostgreSQL | 14.23 | 16.14 | None — `pg_dump`/`pg_restore` migration verified clean, all 54 tables + data intact |
| Python | 3.12.12 (deadsnakes PPA) | 3.12.3 (Ubuntu 24.04 default) | None — same minor version family |
| Environment | Kubernetes pod (capability-restricted) | Real KVM VM (genuinely unrestricted) | This is the entire point of the migration — real Kubernetes now works |
| Network | Unrestricted | DPI-blocks GitHub/Docker Hub/PyPI/GCS/Quay/Snapcraft | Requires the workarounds documented in the header note |

---

## 12. Old Node Status

The previous CPU node (`216.48.191.142`, access key `~/.ssh/gpu_new_key`) remains **running and completely untouched** as of this migration — no services were stopped, no files were modified, no data was deleted. It is kept available as a rollback/reference point pending a separate, explicit decommission decision (not part of this migration). See `implementation/BACKLOG.md`'s TT-014 (resolved) for the full history of why it could never run Kubernetes.

---

## 13. Observability (Sprint-027, V7 Ch7-10)

**Namespace:** `voiceos-ops` (same namespace as PolicyEngine/Auth/Authz/AIGovernance).

### 13.1 Deployed components

| Component | Image | Purpose |
|---|---|---|
| Prometheus | `prom/prometheus:v2.54.1` | Scrapes all 26 VoiceOS service pods + itself; 30d retention |
| Grafana | `grafana/grafana:11.2.2` | 15 provisioned dashboards (see §13.3); datasources: Prometheus, Loki, Jaeger |
| Alertmanager | `prom/alertmanager:v0.27.0` | Routes CRITICAL→PagerDuty, WARNING→JIRA, INFO→Slack |
| Loki | `grafana/loki:3.1.1` | Centralized log storage, 30d retention (DPDP-driven) |
| FluentBit | `fluent/fluent-bit:3.1.9` (DaemonSet) | Tails every pod's container log, forwards to Loki |
| OTel Collector | `otel/opentelemetry-collector-contrib:0.111.0` | Receives OTLP traces, exports to Jaeger |
| Jaeger | `jaegertracing/all-in-one:1.60` | Trace storage (badger, 7d retention) + query UI |

All 7 images pulled successfully via the existing `mirror.gcr.io` docker.io routing (§0's network note) — no new workaround needed.

Manifests: `infra/k8s/observability/*.yaml`. Deploy/redeploy: `bash infra/k8s/observability/deploy.sh` (generates every ConfigMap from `monitoring/` at deploy time — never hand-copied into the YAML manifests — and applies the Deployments/Services/NetworkPolicy). Requires `GRAFANA_ADMIN_PASSWORD` (from Vault, `secret/voiceos/grafana_admin`) and `PAGERDUTY_SERVICE_KEY`/`JIRA_WEBHOOK_URL`/`SLACK_WEBHOOK_URL` (no real accounts exist for this project — falls back to an obvious placeholder so Alertmanager still starts).

### 13.2 Endpoints (in-cluster Service DNS)

| Variable | Value |
|---|---|
| `PROMETHEUS_ENDPOINT` | `http://prometheus.voiceos-ops.svc.cluster.local:9090` |
| `GRAFANA_ENDPOINT` | `http://grafana.voiceos-ops.svc.cluster.local:3000` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector.voiceos-ops.svc.cluster.local:4317` (gRPC) / `:4318` (HTTP) |
| `JAEGER_ENDPOINT` | `http://jaeger-query.voiceos-ops.svc.cluster.local:16686` |
| Loki | `http://loki.voiceos-ops.svc.cluster.local:3100` |
| Alertmanager | `http://alertmanager.voiceos-ops.svc.cluster.local:9093` |

### 13.3 Prometheus scrape targets and recording rules

- **Scrape jobs** (`monitoring/prometheus/prometheus.yml`): `prometheus` (self), `kubernetes-apiservers`/`kubernetes-nodes-cadvisor`/`voiceos-services` (pod-annotation SD — configured but non-functional today, see §13.5), `voiceos-services-static` (file-based, **the actually-functional path** — `monitoring/prometheus/targets/dev.yml`, 26/26 targets `up`), `gpu-node` (direct scrape of `217.18.55.96:8100/8000/8200` — down, GPU services never bound `/metrics`), `node-exporter`/`postgres-exporter`/`redis-exporter` (configured, not deployed this sprint — aspirational).
- **Recording rules** (`monitoring/prometheus/recording_rules.yml`): `voiceos:first_audio_seconds:p95_{5m,30m,1h,6h}`, `voiceos:first_audio_slo:burn_rate_{5m,1h,6h}`, `voiceos:availability:ratio_{5m,30m,1h,6h}`, `voiceos:availability_slo:burn_rate_{5m,1h,6h}`, `voiceos:ptp_rate:ratio_1h`, `voiceos:campaign_completion_rate:ratio_1h` — 8 groups / 34 rules total, all loaded successfully (verified via `/api/v1/rules`).
- **Alert rules**: `monitoring/prometheus/alert_rules/{slo_alerts,infrastructure,application,business}.yml` — SLO burn-rate (fast/slow), node/GPU/fleet-health down, error-rate/circuit-breaker/DLQ, PTP-rate/campaign-completion drops.

### 13.4 Grafana dashboard inventory

`monitoring/grafana/dashboards/`: `slo-overview`, `call-funnel`, `gpu-fleet`, `latency-breakdown`, `reliability`, `business` (6); `governance/`: `consent-coverage`, `policy-violations`, `ai-incidents`, `data-retention`, `break-glass-usage` (5); `security/`: `security-kpis`, `vulnerability-tracker`, `auth-anomalies`, `threat-detection` (4). All 15 provisioned automatically on Grafana startup (`monitoring/grafana/provisioning/`) — verified via `GET /api/search` returning all 15.

### 13.5 Known limitations (found live, Sprint-027 Phase 2)

- **GPU node metrics (3 targets down):** STT/LLM/TTS on the GPU node never had a `/metrics` HTTP endpoint bound (same class of gap as TT-006 — these processes exist and serve inference, they just never wired `prometheus_client` to an exposed route). Not fixed this session (GPU node access requires the user's explicit approval each time, per standing rule). Filed as a note under BACKLOG.md's TT-006.
- **Kubernetes-SD jobs non-functional (extends TT-015):** `kubernetes-apiservers`/`kubernetes-nodes-cadvisor`/`voiceos-services` (pod-annotation SD) all depend on reaching `kubernetes.default.svc`'s ClusterIP (`10.96.0.1`), which kube-proxy DNATs to the apiserver's advertised address `10.0.2.2` — NAT-internal to the hypervisor, exactly the address TT-015 already documented as unroutable from the GPU node. It turns out this **also** blocks in-cluster pods (confirmed with Prometheus and FluentBit, both running on this same control-plane node) from reaching the apiserver via its ClusterIP — a broader instance of the same root cause, not limited to cross-provider traffic. The actually-functional scrape path is the static `voiceos-services-static` job.
- **FluentBit's `kubernetes` filter removed:** its constant 10s-interval retry against the unreachable apiserver was found to starve FluentBit's own DNS resolution for its Loki output in the same process — with the filter enabled, zero log records ever reached Loki even though tailing worked correctly. Log lines are parsed directly off the `cri`-parsed `log` field instead (no pod-metadata enrichment until the apiserver NAT issue is resolved).
- **node-exporter/postgres-exporter/redis-exporter:** configured as Prometheus scrape jobs but not deployed as K8s workloads this sprint (aspirational/documented, not required by Sprint-027.md's core AC).

### 13.6 Health check commands

```bash
curl -sf http://prometheus.voiceos-ops.svc.cluster.local:9090/-/ready
curl -sf http://prometheus.voiceos-ops.svc.cluster.local:9090/api/v1/targets | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(sum(1 for t in d['data']['activeTargets'] if t['health']=='up'))"
curl -sf http://grafana.voiceos-ops.svc.cluster.local:3000/api/health
curl -sf http://loki.voiceos-ops.svc.cluster.local:3100/ready
curl -sf http://jaeger-query.voiceos-ops.svc.cluster.local:16686/jaeger
curl -sf http://alertmanager.voiceos-ops.svc.cluster.local:9093/-/ready
```

All wired into `deployment/cpu/healthcheck.sh`'s new "Observability Stack" section (also runs a `GPUFleetHealthMonitor`/`CostOptimizer`/`OpsAnalytics` library construction smoke test).

---

*This document is updated after every sprint. It replaces the prior version of this document, which described the now-superseded `216.48.191.142` node — see git history / CHANGELOG.md for that node's full historical record if needed.*
