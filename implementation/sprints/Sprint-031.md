# Sprint-031 — Production Release

**Epic:** E8 — Founder Validation → Pilot → Production Release  
**Status:** ⬜ Pending  
**Depends on:** Sprint-030, Sprint-032, Sprint-033, Sprint-034  
**Blocks:** None (final sprint)  
**Milestone:** Production Release — M-10  

---

## Objective

Full production launch: unrestricted tenant onboarding open, public API live, all SLO dashboards operational, runbooks finalized and distributed, support processes active, release notes and customer documentation published, CHANGELOG updated to v1.0.0.

---

## Architecture References

- Volume 7: Ch16 (Release Management — canary complete, version tagging), Ch17 (Global Scaling — future path), Ch21 (Enterprise Operations — support processes), Ch22 (Enterprise Operations — dedicated cluster ops, premium SLAs, air-gapped deployment update procedures)
- Volume 4: Ch22 (Governance Runbooks — security-specific playbooks for breach, leak, AI incident, credential compromise, each with regulatory notification timelines)
- DocSuite-09: Deployment Cookbook

---

## Deliverables

### 1. Production Deployment (canary complete)
- Traffic already at 100% from Sprint-028 canary
- Sprint-031 deployment: any final fixes from pilot + production hardening
- Zero-downtime rolling update for any changes from pilot remediation
- Tag production image: `v1.0.0`

### 2. Release Notes & Customer Documentation

**`docs/releases/v1.0.0-release-notes.md`:**
- What's new (first release — summarize capabilities)
- Known limitations (document any deferred enterprise/hyper-scale features)
- Breaking changes: N/A (first release)
- Upgrade path: N/A

**Customer-facing documentation (published at docs endpoint or static site):**
- `docs/customer/quickstart.md` — first call in 30 minutes
- `docs/customer/api-reference.md` — public API usage guide (generated from OpenAPI spec)
- `docs/customer/webhook-integration.md` — webhook setup and payload reference
- `docs/customer/compliance-guide.md` — RBI/DPDP compliance configuration guide
- `docs/customer/campaign-setup.md` — end-to-end campaign creation guide
- `docs/customer/troubleshooting.md` — common issues and resolutions

### 3. Operational Runbooks (finalized from Sprint-030 drafts)

**`docs/operations/`:**
- `incident-response.md` — full incident management playbook (CRITICAL/HIGH/MEDIUM workflow)
- `on-call-runbook.md` — who does what, alert interpretation, escalation contacts
- `rollback-procedure.md` — canary rollback, per-service rollback, emergency rollback
- `common-failure-modes.md` — top 10 failure modes + resolution steps
- `capacity-planning.md` — how to scale (add GPU nodes, scale Redis, increase DB connections)
- `tenant-onboarding.md` — how to provision and configure a new tenant

### 4. Governance Runbooks — Security-Specific (V4 Ch22)

**`docs/security/runbooks/`:**
- `data-breach-response.md` — step-by-step data breach playbook: contain → assess → notify regulators (DPDP: 72h) → notify affected users → post-incident review
- `privacy-incident-response.md` — PII leak / unauthorized disclosure playbook: revoke access → crypto-shred affected DEK → audit trail → DPDP notification assessment
- `ai-misbehavior-response.md` — AI incident playbook: halt affected campaign → pull call logs → forensic analysis via DecisionEnvelope lineage → model governance board review
- `credential-compromise-response.md` — compromised API key / service credential: emergency revoke → rotate → audit access history → affected tenant notification
- `compliance-violation-response.md` — RBI/DPDP violation detected: escalate to compliance officer → document → remediate → regulatory self-report if required

Each runbook includes: detection signals, immediate actions, notification chain, timeline requirements, post-incident review checklist.

### 5. Enterprise Operations Procedures (V7 Ch22)

**`docs/enterprise/`:**
- `dedicated-cluster-ops.md` — provisioning and operating dedicated customer clusters (Enterprise tier, Sprint-032): Terraform module usage, health checks, maintenance windows
- `premium-sla-delivery.md` — how to meet ENTERPRISE tier SLAs: dedicated on-call contact, 4h response, proactive monitoring, monthly SLA reports
- `air-gapped-deployment.md` — update procedure for air-gapped enterprise deployments: bundle preparation, image transfer, validation, cutover checklist
- `enterprise-support-channel.md` — ENTERPRISE support channel operations: ticket routing, escalation to engineering on-call, customer-specific maintenance schedule

### 4. Support Process Activation

- Support ticket system configured (email/Slack/Jira integration)
- SLA tiers: GROWTH (24h response), ENTERPRISE (4h response, dedicated contact)
- Escalation path from support → engineering on-call defined
- Status page configured (statuspage.io or equivalent) — shows SLO metrics publicly

### 5. Final Tracking Document Updates

**`CHANGELOG.md` (implementation/CHANGELOG.md and repo root):**
```markdown
## [1.0.0] — 2026-XX-XX

### Added
- Complete VoiceOS v2 production release
- Real-time Hindi/Hinglish/English voice collections platform
- First-audio p95 ≤ 1.5s (production validated)
- Law-of-Authority enforcement (zero LLM hallucinations in production)
- RBI + DPDP compliance by construction
- Multi-tenant SaaS platform
- [full capability list...]

### Changed
- N/A (first release)
```

**`PROJECT_STATUS.md`:**
- Phase: Production (was: In Development)
- Sprint: 31/31 Complete
- All production readiness areas: ✅

---

## Files Expected to Change

**New:** `docs/releases/v1.0.0-release-notes.md`, `docs/customer/` (all guides), `docs/operations/` (finalized runbooks)  
**New:** `docs/security/runbooks/` (5 governance runbooks: data-breach, privacy-incident, ai-misbehavior, credential-compromise, compliance-violation)  
**New:** `docs/enterprise/` (4 enterprise operations guides: dedicated-cluster-ops, premium-sla-delivery, air-gapped-deployment, enterprise-support-channel)  
**Modified:** `implementation/CHANGELOG.md` → v1.0.0 entry  
**Modified:** `PROJECT_STATUS.md` → 34/34 complete, Phase = Production  
**Modified:** `implementation/BACKLOG.md` → all sprints = ✅ Done  
**Modified:** `implementation/DONE.md` → Sprint-031 added

---

## Acceptance Criteria

- [ ] Production deployment complete, image tagged `v1.0.0`
- [ ] All SLO dashboards showing live production data with no active alerts
- [ ] Customer-facing API documentation published and accessible
- [ ] All 6 customer documentation files exist and reviewed
- [ ] All operational runbooks finalized and distributed to on-call team
- [ ] Support process active (ticket system, SLA configured, status page live)
- [ ] `CHANGELOG.md` updated to v1.0.0
- [ ] `PROJECT_STATUS.md`: 34/34 sprints complete, Phase = Production
- [ ] No outstanding CRITICAL or HIGH issues
- [ ] All 5 governance runbooks present in `docs/security/runbooks/` with regulatory timelines documented
- [ ] All 4 enterprise operations guides present in `docs/enterprise/`

---

## Definition of Done

- [ ] All AC items checked
- [ ] v1.0.0 git tag created on main branch
- [ ] All customer documentation accessible
- [ ] Operational runbooks distributed
- [ ] Support process verified (test ticket end-to-end)
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated to final state
- [ ] **Milestone M-10 (Production Release) verified**

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Sprint-031 is primarily documentation, release engineering, and production hardening. All software was completed by Sprint-028. Phase 1 consists of producing all documentation artifacts, validating their completeness, and applying any pilot-remediation code fixes with full test suite passage.

### Files Created

- `docs/releases/v1.0.0-release-notes.md`
- `docs/customer/quickstart.md`, `api-reference.md`, `webhook-integration.md`, `compliance-guide.md`, `campaign-setup.md`, `troubleshooting.md`
- `docs/operations/incident-response.md`, `on-call-runbook.md` (finalized), `rollback-procedure.md` (finalized), `common-failure-modes.md`, `capacity-planning.md`, `tenant-onboarding.md`
- `docs/security/runbooks/data-breach-response.md`, `privacy-incident-response.md`, `ai-misbehavior-response.md`, `credential-compromise-response.md`, `compliance-violation-response.md`
- `docs/enterprise/dedicated-cluster-ops.md`, `premium-sla-delivery.md`, `air-gapped-deployment.md`, `enterprise-support-channel.md`

### Mock Backends Used

> None. Phase 1 for this sprint is documentation and release preparation.

### Validations

| Check | What | Expected |
|---|---|---|
| Documentation completeness | `ls docs/customer/ docs/operations/ docs/security/runbooks/ docs/enterprise/` | All required files present |
| Governance runbooks | Each runbook has regulatory timeline section | DPDP 72h notification present in data-breach runbook |
| Release notes | `docs/releases/v1.0.0-release-notes.md` | Version, capabilities, known limitations, upgrade path |
| Pilot remediation code | `pytest tests/` | All tests pass after pilot fixes applied |
| Static analysis | `ruff check src/ tests/` | 0 errors |

### Expected Outputs

- All 6 customer documentation files present and reviewed for accuracy
- All 6 operational runbook files finalized (no TODOs remaining)
- All 5 governance runbooks with detection signals, immediate actions, notification chain, timeline requirements
- All 4 enterprise operations guides with step-by-step procedures
- `CHANGELOG.md` v1.0.0 entry drafted
- All pilot-remediation code fixes pass full test suite

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services active this sprint (all previously deployed):**
- All Sprint-004–030 services remain running
- Any pilot-remediation fixes applied as zero-downtime rolling updates

**Deployment procedure:**
1. Apply any pilot-remediation code changes: `helm upgrade voiceos-platform` with new image tags
2. Tag production image: `docker tag voiceos-platform:latest voiceos-platform:v1.0.0` + push to registry
3. Create git tag: `git tag -a v1.0.0 -m "VoiceOS v2 — Production Release"`
4. Verify support ticket system end-to-end: create test ticket → routed correctly → response within SLA
5. Publish customer documentation to docs endpoint
6. Set status page live

**Health checks:**
- All services healthy post-update: `kubectl get pods --all-namespaces` → all Running
- No active CRITICAL or HIGH alerts on Grafana SLO dashboards
- SLO attainment: availability ≥ 99.95%, first-audio p95 ≤ 1.5s (live data)

**Integration validation:**
- Test new tenant onboarding via admin portal: TRIAL → PRODUCTION (KEK created, first campaign runnable)
- Public API accessible with API key: `GET /v1/customers/{id}` → 200
- Webhook delivery: test event → signed delivery received by test endpoint
- Support ticket: test GROWTH tier and ENTERPRISE tier response paths

**Rollback procedure:**
- Rolling update: `kubectl rollout undo deployment/<service>` per service if issues after update
- Tag v1.0.0 is immutable; rollback targets previous Helm revision

### GPU Node

**GPU active throughout production release:**

| Model | VRAM | Role |
|---|---|---|
| Whisper Large-v3 | 6,144 MB | STT — all production calls |
| Qwen2.5-7B via vLLM | 16,384 MB | LLM — all production calls |
| Veena TTS | 2,048 MB | TTS — all production calls |

GPU fleet must remain fully operational at v1.0.0 production launch. GPU health is monitored continuously via Grafana gpu-fleet dashboard.

### Infrastructure Validation

**CPU Validation:**
- All services: `GET /health/ready` → 200 post-update
- SLO dashboards: real production data, no active alerts, error budget > 95%
- Tenant onboarding: new tenant provisioned end-to-end in < 5 minutes

**GPU Validation:**
- First-audio p95 ≤ 1.5s maintained on production with real calls
- GPU fleet health score = 1.0 (all nodes healthy)

**Networking Validation:**
- Customer documentation accessible externally (docs endpoint reachable)
- Status page live and shows correct SLO metrics

### Regression Validation

- Full regression suite passes post-pilot-remediation updates
- No new issues introduced by v1.0.0 release changes
- All governance runbooks reviewed and signed by engineering lead

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] All 6 customer documentation files complete and reviewed
- [ ] All 6 operational runbooks finalized
- [ ] All 5 governance runbooks complete with regulatory timelines
- [ ] All 4 enterprise operations guides complete
- [ ] v1.0.0 release notes drafted
- [ ] Pilot-remediation code fixes pass full test suite
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] Production image tagged `v1.0.0` and pushed to registry
- [ ] Git tag `v1.0.0` created on main branch
- [ ] All services healthy post-release update
- [ ] SLO dashboards showing live data, no active alerts
- [ ] Customer documentation published and accessible
- [ ] Support process live (test ticket end-to-end verified)
- [ ] Status page live
- [ ] Milestone M-10 (Production Release) verified
- [ ] `PROJECT_STATUS.md`: 34/34 sprints complete, Phase = Production

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. This is the **final infrastructure snapshot** — the production v1.0.0 state. Both documents must be complete enough that any new engineer can rebuild the entire production system from scratch using only these files.

### CPU_NODE_STATE.md — Updates This Sprint

- Update header: version `v1.0.0 — Production Release`; `last_updated: Sprint-031`
- Update §8.1 Services table: add production `v1.0.0` image tags to all services; mark all as production-stable
- Update §8.4: note pilot-period hotfixes applied in rolling update; document final pod counts per namespace
- Update §14 Health Check Commands: final production health check sequence documented end-to-end
- Update §15 Rollback Commands: v1.0.0 rollback documented as: rollout undo to `production-alpha` tag
- Update §16 Verification Commands: add `git tag v1.0.0 present` check

### GPU_NODE_STATE.md — Updates This Sprint

- Update header: version `v1.0.0 — Production Release`; `last_updated: Sprint-031`
- Update §8: all GPU models tagged as production-stable
- Update §17 VRAM Budget: final production-validated peak VRAM documented
- Confirm: serving pool (Whisper + Qwen2.5-7B + Veena) completely unchanged from Sprint-030 pilot state

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/restore.sh` | Final production version: all services, namespaces, migrations, health checks verified |
| `deployment/gpu/restore.sh` | Final production version: model downloads, service startup, latency validation verified |
| `deployment/cpu/healthcheck.sh` | Final list of all services across all 4 namespaces |
| `deployment/gpu/healthcheck.sh` | Final: VRAM budget, all three model health checks |
| `deployment/gpu/model_manifest.yaml` | Final: all three models with production-validated download commands and latency targets |
| `deployment/cpu/.env.example` | Final: all environment variable names documented |
| `deployment/gpu/.env.example` | Final: all GPU environment variable names documented |

### DR Validation

**Production rebuild (full end-to-end — the definitive DR test):**
```bash
# CPU node — fresh server
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: 34 sprints worth of services fully operational

# GPU node — fresh server
sudo bash deployment/gpu/bootstrap.sh
bash deployment/gpu/restore.sh
bash deployment/gpu/healthcheck.sh
# Expected: Whisper + Qwen2.5-7B + Veena running; VRAM ≤ 24,576MB; latency targets met
```

**Production v1.0.0 validation:**
```bash
# Full regression suite on rebuilt infrastructure
pytest tests/integration/ -m regression -v
pytest tests/compliance/ -v
pytest tests/evaluation/ -v

# End-to-end call
python3 scripts/validate/walking_skeleton.py --calls 10
# Expected: first-audio p95 ≤ 1.5s; 0 LoA violations; all SLO targets met
```

**Git tag verification:**
```bash
git tag --list | grep v1.0.0
# Expected: v1.0.0 present and points to main branch HEAD
```

---

## Post-Release Roadmap (out of scope for Sprint-031)

The following features are deliberately deferred to avoid scope overrun and are documented as future work:
- Multi-region deployment (V7 Ch17 Hyper-Scale)
- Additional language support beyond Hindi/Hinglish/English
- Hyper-scale GPU fleet (beyond 50-node configurations)

These are to be addressed via future ADRs and sprints after production stability is confirmed.

**Note:** Enterprise Platform (V5 Ch22 — SSO/SCIM), Workflow Automation (V5 Ch17), Customer Success Platform (V5 Ch18), and Conversation Learning Layer (V2 Ch18) are implemented in Sprints 032–034, which are prerequisites for this sprint.

---

*This is the final sprint. VoiceOS v2 — built to its architecture, one sprint at a time.*
