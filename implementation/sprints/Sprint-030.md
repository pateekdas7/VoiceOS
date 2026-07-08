# Sprint-030 — Pilot Deployment

**Epic:** E8 — Founder Validation → Pilot → Production Release  
**Status:** ⬜ Pending  
**Depends on:** Sprint-029  
**Blocks:** Sprint-031  
**Milestone:** Pilot Deployment — M-9  

---

## Objective

Controlled live-production pilot: 10–20 real borrower accounts with genuine outstanding loans, live AI calls, real-time monitoring, rapid issue triage with SLA commitments, and clear exit criteria for full production release.

---

## Architecture References

- Volume 7: Ch1 (SLOs: availability ≥ 99.95%, first-audio p95 ≤ 1.5s, RPO ≤ 5 min, RTO ≤ 30 min), Ch16 (Release Management), Ch18 (Incident Management)
- DocSuite-09: Deployment Cookbook (operational procedures)
- DocSuite-10: AI Evaluation Handbook (quality monitoring)

---

## Pilot Preparation

### Cohort Selection Criteria
- 10–20 borrowers with outstanding loans (real accounts)
- Consent-verified: recording consent obtained via separate channel before pilot
- Low-risk profile: mid-range DPD (30–90 days), no active disputes, no hardship marker
- Single campaign: one product type, one EMI structure (minimal complexity)
- Language: Hindi or Hinglish (primary evaluation language)

### Operational Readiness Checklist (before first call)
- [ ] On-call rotation established: 2 engineers, 1 supervisor available during call windows
- [ ] PagerDuty/equivalent on-call configured and tested
- [ ] Incident runbook reviewed by all on-call participants
- [ ] Supervisor dashboard live (Sprint-023 contact center)
- [ ] Real-time monitoring dashboard active (Sprint-027 Grafana)
- [ ] All SLO alerts verified (test alert → receives PagerDuty notification)
- [ ] Rollback procedure documented and tested (can roll back in < 5 min)
- [ ] Customer contact list approved by compliance officer
- [ ] Legal sign-off on AI disclosure script and consent process

---

## Pilot Execution

### Call Windows
- 08:00–18:00 local time (conservative RBI buffer)
- Maximum 2 attempts per borrower per day
- Pilot duration: 5–7 business days

### Daily Monitoring Cadence
- 09:00: Review previous day's call outcomes, issues, SLO compliance
- 12:00: Mid-day check of SLO dashboards; escalate if warning threshold breached
- 18:00: End-of-day review; document in pilot report
- Continuous: on-call alert monitoring

### Issue Triage SLA
| Severity | Definition | Response Time | Resolution Time |
|---|---|---|---|
| CRITICAL | Data breach, regulatory violation, system unavailable | Immediate (< 15 min) | < 2 hours |
| HIGH | SLO breach, repeated AI errors, customer complaint | < 1 hour | < 24 hours |
| MEDIUM | Occasional AI quality issue, non-critical bug | < 4 hours | Next sprint |
| LOW | Minor UI, cosmetic, documentation | < 1 day | Backlog |

---

## Exit Criteria (all must pass to proceed to Sprint-031)

| # | Criterion | Target |
|---|---|---|
| 1 | System intervention rate | ≤ 5% of calls require supervisor intervention |
| 2 | Availability | ≥ 99.95% during pilot window |
| 3 | First-audio latency | p95 ≤ 1.5s maintained throughout |
| 4 | Regulatory violations | Zero (zero calls outside hours, zero without consent) |
| 5 | Data breaches | Zero |
| 6 | PTP conversion rate | Within ±20% of historical human-agent benchmark |
| 7 | Customer complaints | Zero formal complaints requiring escalation |
| 8 | Critical issues | Zero unresolved CRITICAL issues at pilot end |

---

## Pilot Artifacts

**`evaluation/pilot-report.md`** must include:
- Pilot period (start + end dates)
- Number of borrowers contacted, calls made, calls completed
- Outcome breakdown: PTP created / callback scheduled / dispute raised / unsuccessful
- Per-day SLO compliance (availability, latency)
- Issues encountered (by severity), resolution status
- Exit criteria assessment (PASS / FAIL per criterion)
- Overall pilot verdict (READY FOR PRODUCTION / NOT READY)
- Pilot team sign-off

---

## Files Expected to Change

**New:** `evaluation/pilot-report.md`  
**New:** `docs/operations/on-call-runbook.md` — incident response procedures for production
**New:** `docs/operations/rollback-procedure.md` — step-by-step production rollback

---

## Acceptance Criteria

- [ ] All operational readiness checklist items checked before first pilot call
- [ ] Daily monitoring cadence followed and documented throughout pilot
- [ ] All exit criteria assessed and all PASS
- [ ] `evaluation/pilot-report.md` complete with pilot team sign-off
- [ ] Zero CRITICAL issues unresolved
- [ ] On-call runbook and rollback procedure documented

---

## Definition of Done

- [ ] All AC items checked
- [ ] All exit criteria PASS
- [ ] Pilot report committed with sign-off
- [ ] **Milestone M-9 (Pilot Deployment) criteria verified**
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-031

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Pilot runbooks, on-call documentation, rollback procedures, and pilot report template are written. Operational readiness checklist is finalized. No code implementation is required — all software was completed by Sprint-028.

### Files Created

- `evaluation/pilot-report.md` (template with all required sections)
- `docs/operations/on-call-runbook.md` — incident response procedures
- `docs/operations/rollback-procedure.md` — step-by-step production rollback

### Mock Backends Used

> None. Phase 1 for this sprint is documentation-only.

### Validations

| Check | What | Expected |
|---|---|---|
| Operational readiness checklist | Manual review of all checklist items | Each item has a defined responsible owner and verification method |
| Pilot report template | Ensure all required sections present | All 8 exit criteria are defined with measurable targets |
| Rollback procedure | Dry-run review | Can be executed in < 5 min (step count and time estimate verified) |
| On-call runbook | Review against incident SLA table | Response/resolution times match severity table in this sprint |

### Expected Outputs

- `evaluation/pilot-report.md`: all sections templated; exit criteria table with targets from this sprint
- `docs/operations/on-call-runbook.md`: covers all 4 severity levels, escalation paths, PagerDuty setup
- `docs/operations/rollback-procedure.md`: step-by-step with estimated time < 5 min

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 is the pilot itself. Phase 2 begins only after Phase 1 (documentation complete) and the Operational Readiness Checklist is fully signed off.

### CPU Node

**Services active this sprint (all previously deployed):**
- All Sprint-004–028 services remain running
- No new service deployments this sprint

**Pilot execution procedure:**
1. Complete all Operational Readiness Checklist items before first call
2. Execute calls within 08:00–18:00 window; ≤ 2 attempts/borrower/day
3. Follow daily monitoring cadence (09:00, 12:00, 18:00)
4. Triage all issues per severity SLA
5. After 5–7 business days: assess all 8 exit criteria
6. Commit `evaluation/pilot-report.md` with pilot team sign-off

**Health checks (continuous during pilot):**
- Availability: `voiceos_availability_ratio` Prometheus gauge ≥ 0.9995
- First-audio p95: Grafana SLO dashboard ≤ 1.5s throughout
- PagerDuty: alerts routing correctly for all CRITICAL and HIGH events

**Rollback procedure (< 5 min):**
- Stop campaign scheduler: `POST /campaigns/{id}/pause` → no new calls dispatched
- If infrastructure failure: `helm rollback voiceos-platform <prior-revision>`
- Document in incident log

### GPU Node

**GPU active throughout pilot:**

| Model | VRAM | Role |
|---|---|---|
| Whisper Large-v3 | 6,144 MB | STT for all pilot calls |
| Qwen2.5-7B via vLLM | 16,384 MB | LLM for all pilot calls |
| Veena TTS | 2,048 MB | TTS for all pilot calls |

GPU fleet must remain fully operational throughout the pilot window. Any GPU node failure triggers CRITICAL incident and on-call escalation.

### Infrastructure Validation

**CPU Validation:**
- Availability ≥ 99.95% across all pilot business days
- First-audio p95 ≤ 1.5s across all pilot calls (from production traces)
- Zero regulatory violations: all calls within 08:00–18:00; all borrowers consent-verified

**GPU Validation:**
- No GPU-related call failures during pilot window
- GPU utilization within safe range (monitoring dashboards active)

**Networking Validation:**
> Existing networking infrastructure from Sprint-028 remains validated.

### Regression Validation

- All Sprint-028 regression suites pass at start of pilot
- Issue triage log: all CRITICAL and HIGH issues resolved within SLA
- Exit criterion 8: zero CRITICAL issues unresolved at pilot end

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] `evaluation/pilot-report.md` template complete
- [ ] `docs/operations/on-call-runbook.md` complete
- [ ] `docs/operations/rollback-procedure.md` complete (< 5 min rollback)
- [ ] Operational readiness checklist finalized with owner assignments
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All operational readiness checklist items signed off before first call
- [ ] Daily monitoring cadence followed throughout pilot period
- [ ] All 8 exit criteria assessed and PASS
- [ ] Pilot report committed with pilot team sign-off
- [ ] Zero CRITICAL issues unresolved
- [ ] Milestone M-9 (Pilot Deployment) verified
- [ ] Deployment remains active as baseline for Sprint-031

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-030 is the Pilot — real borrower calls are running on this infrastructure. This snapshot must be sufficient to restore the pilot environment exactly.

### CPU_NODE_STATE.md — Updates This Sprint

- Update §8.1: all services tagged with `pilot-validated` in notes column
- Add §8.4 Pilot Notes: document any pilot-period hotfixes (rolling update procedures actually executed during pilot)
- Update §14 Health Check Commands: add pilot-period monitoring command (`kubectl get pods -A | grep -v Running` → zero non-running pods)
- Update §15 Rollback Commands: add pilot rollback procedure (validated during pilot standby)
- Add `PILOT_TENANT_IDS` to §11 Environment Variables (list of pilot tenant IDs — not secrets)

### GPU_NODE_STATE.md — Updates This Sprint

- Update §8 Deployed AI Models: note pilot-period VRAM stability observed (no memory leaks over multi-day pilot)
- Update §16 VRAM Budget: add pilot-period peak VRAM measurement
- Update `GPU_NODE_STATE.md` last_updated: Sprint-030

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/restore.sh` | Add: post-restore pilot-tenant seed validation (verify pilot tenant configs present) |
| `deployment/cpu/healthcheck.sh` | Add: pilot period extended health check — `kubectl top pods -n voiceos-runtime` (CPU/memory within bounds) |

### DR Validation

**Pilot environment rebuild (must succeed before pilot calls begin):**
```bash
# CPU node
sudo bash deployment/cpu/bootstrap.sh
bash deployment/cpu/restore.sh
bash deployment/cpu/healthcheck.sh
# Expected: all 30+ services healthy; pilot tenants configured

# GPU node
sudo bash deployment/gpu/bootstrap.sh
bash deployment/gpu/restore.sh
# Expected: Whisper + Qwen2.5 + Veena all running; VRAM ≤ 24,576MB; latency targets met
```

**Cross-node communication after rebuild:**
```bash
python3 scripts/validate/walking_skeleton.py --calls 5
# Expected: end-to-end call completes; first-audio p95 ≤ 1.5s
```

**Monitoring stack operational:**
```bash
curl -sf http://<prometheus-host>/api/v1/query?query=up | jq '.data.result | length'
# Expected: all scrape targets up
curl -sf http://<grafana-host>/api/health
# Expected: {"database": "ok"}
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; system ready for pilot calls
```
