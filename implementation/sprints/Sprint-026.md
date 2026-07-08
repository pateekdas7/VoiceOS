# Sprint-026 — Infrastructure as Code & Kubernetes Architecture

**Epic:** E7 — Production Alpha  
**Status:** ⬜ Pending  
**Depends on:** Sprint-016, Sprint-020, Sprint-025  
**Blocks:** Sprint-027  

---

## Objective

Implement the complete Infrastructure as Code (Terraform + Helm + Kubernetes) and the production Kubernetes architecture — node pools, GPU scheduling integration, resource guarantees, network policies, and pod disruption budgets. After this sprint, the entire system can be deployed reproducibly with a single command.

---

## Architecture References

- Volume 7: Ch2 (Production Topology — single-node/multi-node/GPU-cluster/regional shapes), Ch3 (Infrastructure as Code — Terraform/Ansible/Helm, IaC-3: no manual prod changes), Ch5 (Kubernetes Architecture — node pools, Guaranteed QoS K8S-1, GPU taint K8S-2, PDBs)
- Volume 5: Ch23 (SaaS Operations — feature flag system, fleet version rollout rings, tenant data migration framework, license entitlement enforcement)
- DocSuite-09: Deployment Cookbook

---

## Components to Implement

### Terraform (`infra/terraform/`)

```
infra/terraform/
├── main.tf                 (root module — calls all sub-modules)
├── variables.tf            (input variables: environment, region, instance types)
├── outputs.tf              (outputs: cluster endpoint, DB connection strings)
├── modules/
│   ├── network/            (VPC, subnets, security groups, NAT gateway)
│   ├── kubernetes/         (GKE/EKS cluster, node pools: cpu, gpu, data, system)
│   ├── database/           (managed Postgres: RDS/Cloud SQL, Multi-AZ)
│   ├── redis/              (managed Redis: ElastiCache/Memorystore, Multi-AZ)
│   ├── mongodb/            (MongoDB Atlas or self-hosted replica set)
│   ├── object-storage/     (S3/GCS buckets: audio recordings, exports, backups)
│   ├── kms/                (KMS key per tenant skeleton + system keys)
│   └── registry/           (Container image registry)
└── environments/
    ├── dev/                (dev environment overrides)
    ├── staging/            (staging environment)
    └── production/         (production environment)
```

**IaC-3 enforcement (no manual prod changes):**
- All Terraform applied via CI/CD only (manual `terraform apply` blocked in prod)
- State stored in remote backend (S3 + DynamoDB state lock)
- Plan always shown and approved before apply

### Helm Charts (`infra/helm/`)

```
infra/helm/
├── voiceos-platform/       (umbrella chart — depends on all service charts)
├── charts/
│   ├── media-gateway/
│   ├── audio-preprocessing/
│   ├── vad-endpointing/
│   ├── gpu-scheduler/
│   ├── stt/
│   ├── llm-runtime/
│   ├── tts/
│   ├── conversation-engine/
│   ├── dialogue-manager/
│   ├── policy-engine/
│   ├── auth/
│   ├── authz/
│   ├── ai-governance/
│   ├── tenant-management/
│   ├── crm/
│   ├── collections/
│   ├── campaign-management/
│   ├── contact-center/
│   ├── billing/
│   ├── metering/
│   ├── analytics/
│   ├── admin-portal/
│   ├── ai-config/
│   ├── integration-platform/
│   └── api-platform/
└── values/
    ├── dev-values.yaml
    ├── staging-values.yaml
    └── production-values.yaml
```

**Each Helm chart must include:**
- `Deployment` with correct resource requests/limits (Guaranteed QoS for hot-path: `requests == limits`)
- `Service` (ClusterIP, with named ports)
- `ConfigMap` (non-secret config)
- `HorizontalPodAutoscaler` (CPU-based for most; custom metrics for GPU-adjacent services)
- `PodDisruptionBudget` (min_available: 1 for all hot-path services)
- `NetworkPolicy` (deny-all default + explicit ingress/egress allow-list)
- `ServiceAccount` (least-privilege)

**GPU node configuration (K8S-2):**
- GPU nodes have taint: `nvidia.com/gpu=true:NoSchedule`
- Only GPU Scheduler pods have toleration for this taint
- GPU Scheduler then manages GPU allocation itself (no direct GPU requests from other pods)

### Kubernetes Cluster Configuration (`infra/k8s/`)

```
infra/k8s/
├── namespaces.yaml         (voiceos-runtime, voiceos-data, voiceos-platform, voiceos-ops)
├── priority-classes.yaml   (CRITICAL > HIGH > NORMAL > LOW)
├── resource-quotas.yaml    (per-namespace resource quotas)
└── cluster-policies/       (OPA/Kyverno policies: no privileged containers, readOnly rootfs)
```

### SaaS Operations Platform (`src/services/saas-ops/`) (V5 Ch23)

```
src/services/saas-ops/
├── __init__.py
├── feature_flags.py        (FeatureFlagService: per-tenant, per-cohort, per-plan flag management)
├── fleet_rollout.py        (FleetRolloutManager: progressive version rollout with tenant ring assignment)
├── migration.py            (TenantDataMigration: durable schema/data migration with tenant isolation)
└── entitlement_ops.py      (EntitlementOpsService: license enforcement and entitlement audit)
```

**FeatureFlagService:**
- `is_enabled(flag_name: str, tenant_id: str) -> bool` — checks flag for specific tenant
- Flag targeting: global → plan-level (ENTERPRISE) → cohort (early-access) → per-tenant
- Flag states: ENABLED | DISABLED | GRADUAL_ROLLOUT (percentage-based)
- Flags stored in Postgres `feature_flags` table; cached in Redis with 30s TTL
- Used to gate experimental features, new AI models, and platform capabilities

**FleetRolloutManager:**
- `assign_rollout_ring(tenant_id: str) -> RolloutRing` — assigns tenant to ring 1 (canary), 2 (early), 3 (general), 4 (laggard)
- `get_version_for_ring(ring: RolloutRing) -> str` — returns current target version for each ring
- Progressive rollout: new version promoted ring-by-ring with health gate between each ring

**TenantDataMigration:**
- `run_migration(migration_id: str, tenant_id: str) -> MigrationResult` — runs a specific migration for one tenant
- Idempotent: migration_id acts as idempotency key
- Locked: per-tenant migration lock prevents concurrent migrations
- Rollback: every migration must declare a `rollback()` procedure

---

## Files Expected to Change

**New:** `infra/terraform/` (all files), `infra/helm/` (all charts), `infra/k8s/` (all configs), `src/services/saas-ops/`  
**Modified:** `.github/workflows/release.yml` — add `terraform plan` + Helm lint + `kubectl apply --dry-run`  
**New:** `tests/unit/services/test_feature_flags.py`, `test_fleet_rollout.py`, `test_tenant_migration.py`

---

## Acceptance Criteria

- [ ] `terraform plan` on dev environment: zero errors, correct resource plan
- [ ] `helm lint` on all charts: zero warnings or errors
- [ ] `helm template | kubectl apply --dry-run=client` on all charts: valid Kubernetes manifests
- [ ] All hot-path services have `requests == limits` (Guaranteed QoS) — verified by `kubectl describe pod`
- [ ] GPU nodes are tainted and only GPU Scheduler pods tolerate the taint
- [ ] Network policies deny-all default present in all namespaces
- [ ] PDBs present for all hot-path services (min_available: 1)
- [ ] `terraform state` stored in remote backend (not local)
- [ ] `FeatureFlagService.is_enabled()` returns correct value based on tenant ring assignment
- [ ] `FleetRolloutManager.assign_rollout_ring()` distributes tenants across rings (no ring is empty in test with ≥4 tenants)
- [ ] `TenantDataMigration.run_migration()` is idempotent (same migration_id twice → one result, no double-apply)

---

## Required Tests

**CI/CD validation (not runtime tests):**
- `terraform validate` — validates HCL syntax
- `terraform plan` — runs against dev environment backend
- `helm lint voiceos-platform` — lints umbrella chart
- `helm unittest` — chart template unit tests (values → expected manifests)
- `kube-score` or `kubeconform` — validate manifests against Kubernetes schemas
- `checkov` or `tfsec` — IaC security scanning (no publicly exposed storage, no overly permissive SGs)

---

## Definition of Done

- [ ] All AC items checked
- [ ] All CI/CD IaC validation checks pass
- [ ] GPU scheduling and Kubernetes requirements from V7 Ch5 implemented
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-027

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** IaC validation runs against dev environment backend (remote state); Helm and Kubernetes validation uses `--dry-run=client` or `kube-score`; SaaS Ops service uses `FakeRedisClient` and `TestPostgres`.

### Files Created

- `infra/terraform/` — complete module tree (network, kubernetes, database, redis, mongodb, object-storage, kms, registry), `main.tf`, `variables.tf`, `outputs.tf`, `environments/dev/`, `environments/staging/`, `environments/production/`
- `infra/helm/voiceos-platform/` — umbrella chart + all 24 service sub-charts; `values/dev-values.yaml`, `staging-values.yaml`, `production-values.yaml`
- `infra/k8s/namespaces.yaml`, `priority-classes.yaml`, `resource-quotas.yaml`, `cluster-policies/`
- `src/services/saas-ops/__init__.py`, `feature_flags.py`, `fleet_rollout.py`, `migration.py`, `entitlement_ops.py`
- `tests/unit/services/test_feature_flags.py`, `test_fleet_rollout.py`, `test_tenant_migration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| Redis (feature flags) | `FakeRedisClient` | 30s TTL cache for feature flags |
| Postgres | `TestPostgres` Docker fixture | Feature flag definitions, migration audit log |
| K8s cluster | `--dry-run=client` + `kubeconform` | Manifest validation without live cluster |
| Terraform backend | Dev remote backend | `terraform plan` against real dev state |

### Validations

| Check | Command | Expected |
|---|---|---|
| Terraform syntax | `terraform validate` | 0 errors |
| Terraform plan | `terraform plan` (dev backend) | Zero errors; correct resource plan |
| Helm lint | `helm lint infra/helm/voiceos-platform/ -f infra/helm/values/dev-values.yaml` | 0 warnings, 0 errors |
| Helm template dry-run | `helm template voiceos-platform infra/helm/voiceos-platform/ | kubectl apply --dry-run=client -f -` | Valid manifests |
| Manifest schema validation | `kubeconform` on all generated manifests | 0 schema violations |
| IaC security scan | `checkov -d infra/terraform/` | 0 high/critical findings |
| SaaS Ops unit tests | `pytest tests/unit/services/test_feature_flags.py tests/unit/services/test_fleet_rollout.py tests/unit/services/test_tenant_migration.py` | All pass |
| Coverage | `pytest --cov=src/services/saas-ops --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `FeatureFlagService.is_enabled()`: flag=ENABLED for tenant ring 1 → True; flag=DISABLED → False
- `FleetRolloutManager.assign_rollout_ring()`: 4 test tenants → distributed across rings 1–4
- `TenantDataMigration.run_migration()`: same `migration_id` twice → 1 migration result (idempotent)
- All Helm charts: every hot-path service has `requests == limits` in manifest (Guaranteed QoS)
- GPU taint: only GPU Scheduler toleration present in GPU service chart templates
- PDBs: `minAvailable: 1` present in all hot-path service charts

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services deployed this sprint:**

| Service | Deployment | Why |
|---|---|---|
| SaaSOpsService | K8s Deployment — `voiceos-platform` | Feature flags, fleet rollout rings, tenant migrations |

**Infrastructure applied this sprint:**
- All 24 Helm charts applied to staging Kubernetes cluster
- Namespace structure confirmed: `voiceos-runtime`, `voiceos-data`, `voiceos-platform`, `voiceos-ops`
- Network policies applied: deny-all default + explicit allow-lists
- PDBs applied to all hot-path services
- Priority classes applied: CRITICAL, HIGH, NORMAL, LOW

**Previously deployed services that remain running:**
- All Sprint-004–025 services

**Deployment procedure:**
1. `terraform apply` on staging environment → provision Kubernetes cluster, Postgres, Redis, MongoDB, KMS
2. `helm upgrade --install voiceos-platform infra/helm/voiceos-platform/ -f infra/helm/values/staging-values.yaml` → deploy all services
3. Verify all pods running: `kubectl get pods --all-namespaces`
4. Verify GPU node taint: `kubectl describe node <gpu-node> | grep Taint`
5. Verify resource requests == limits for hot-path pods: `kubectl describe pod <media-gateway-pod>`

**Health checks:**
- All services: `kubectl get pods -n voiceos-runtime`, `voiceos-data`, `voiceos-platform` → all pods Running
- SaaSOpsService: `GET /health/ready` → 200
- Network policies: deny-all baseline present in all namespaces

**Integration validation:**
- Feature flag: create flag via API → `FeatureFlagService.is_enabled()` returns correct value
- Rollout ring: test tenant assignment via API
- GPU taint: attempt to schedule non-GPU-Scheduler pod to GPU node → SchedulerError (Kubernetes rejection)

**Rollback procedure:**
- `helm rollback voiceos-platform <previous-revision>` — rolls back all charts simultaneously
- Terraform: `terraform apply` with previous state file (infrastructure changes are additive; destructive changes require explicit approval)

### GPU Node

**GPU node validation this sprint:**

| Validation | What to check |
|---|---|
| Taint present | `kubectl describe node <gpu-node>` → `nvidia.com/gpu=true:NoSchedule` |
| Toleration enforcement | Non-GPU-Scheduler pods → Kubernetes rejects scheduling to GPU node |
| GPU services running | STT (Whisper), LLM (Qwen2.5-7B via vLLM), TTS (Veena) all Running |
| VRAM allocation unchanged | 24,576MB total VRAM still correctly allocated |

No new GPU model deployments this sprint. K8S-2 taint/toleration compliance is the focus.

### Infrastructure Validation

**CPU Validation:**
- All hot-path pods: `kubectl get pod <name> -o jsonpath='{.spec.containers[0].resources}'` → requests == limits (Guaranteed QoS)
- PDBs: `kubectl get pdb --all-namespaces` → all hot-path services have `minAvailable: 1`
- Network policy: `kubectl get networkpolicies --all-namespaces` → deny-all present in each namespace

**GPU Validation:**
- Taint/toleration: only GPU Scheduler pod scheduled to GPU node; all others remain on CPU nodes
- Existing GPU services: STT, LLM, TTS all respond correctly after Helm redeploy

**Networking Validation:**
- Cross-namespace service communication: `voiceos-runtime` → `voiceos-platform` allowed only via explicit NetworkPolicy
- Inter-service mTLS: all gRPC traffic between services remains encrypted

### Regression Validation

- Walking skeleton e2e test: passes on Helm-deployed cluster
- Feature flag: `is_enabled()` returns correct value for test tenant
- All 24 services healthy after Helm umbrella chart deployment

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] Complete Terraform module tree implemented
- [ ] All 24 Helm charts implemented (Deployment, Service, ConfigMap, HPA, PDB, NetworkPolicy, ServiceAccount)
- [ ] SaaSOpsService (FeatureFlagService, FleetRolloutManager, TenantDataMigration) implemented
- [ ] `terraform validate`, `helm lint`, `kubeconform`, `checkov`: all pass
- [ ] SaaS Ops unit tests pass; migration idempotency verified
- [ ] Coverage ≥ 85% (SaaS Ops code)
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All services deployed via Helm umbrella chart on staging cluster
- [ ] GPU node taint/toleration enforcement confirmed
- [ ] All hot-path services have Guaranteed QoS (requests == limits)
- [ ] PDBs active for all hot-path services
- [ ] Feature flags functional on deployed API
- [ ] Walking skeleton e2e test passes on Helm-deployed cluster
- [ ] Deployment remains active as baseline for Sprint-027

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-026 is the IaC sprint — the deployment files ARE the infrastructure. This snapshot confirms that `restore.sh` on a fresh server produces a fully working cluster.

### CPU_NODE_STATE.md — Updates This Sprint

- Add `SaaSOpsService` to Services table (§8.1) — namespace: `voiceos-platform`
- **Major update §5 Kubernetes:** Document Helm umbrella chart version, all 24 sub-charts, `helm history` command to verify
- Update §5 Kubernetes: document namespace structure, priority classes, resource quotas, network policies as all now deployed via Helm
- Add `SaaSOpsService` health check (§14)
- Update §9.1 Port Map: confirm all 30+ service ports now managed by Helm charts (no manual port assignments)
- Add Terraform state backend details: S3 bucket + DynamoDB lock table names (§2 Required Packages — add Terraform version)

### GPU_NODE_STATE.md — Updates This Sprint

- Update §7 Kubernetes Integration: confirm GPU taint `nvidia.com/gpu=true:NoSchedule` verified and K8S-2 toleration enforcement confirmed
- Update `GPU_NODE_STATE.md` last_updated: Sprint-026

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/restore.sh` | Add: `helm upgrade --install voiceos-platform` as primary deployment mechanism |
| `deployment/cpu/healthcheck.sh` | Update to use Kubernetes service DNS for all health checks |
| `deployment/gpu/restore.sh` | Add note: GPU K8s taint confirmed; GPU Scheduler pod is only pod tolerated on GPU node |

### DR Validation

**Full IaC rebuild test (primary DR scenario):**
```bash
# Step 1: Provision infrastructure via Terraform
cd /opt/voiceos/app/infra/terraform/environments/production
terraform init
terraform apply -auto-approve

# Step 2: Deploy all services via Helm
helm upgrade --install voiceos-platform \
  /opt/voiceos/app/infra/helm/voiceos-platform/ \
  -f /opt/voiceos/app/infra/helm/values/production-values.yaml \
  --wait --timeout 15m

# Step 3: Verify all namespaces healthy
kubectl get pods --all-namespaces | grep -v Running | grep -v Completed
# Expected: no non-Running pods
```

**GPU taint validation:**
```bash
kubectl describe node <gpu-node> | grep Taint
# Expected: nvidia.com/gpu=true:NoSchedule present
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass on Helm-deployed cluster
```
