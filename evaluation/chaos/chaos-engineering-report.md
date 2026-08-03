# Chaos Engineering Report

**Sprint:** Sprint-028 — Performance Validation, Load Testing, Pen Test & Production Alpha Deploy
**Deliverable:** `evaluation/chaos/` (Sprint-028 §3 — Chaos Engineering)
**Architecture Reference:** Volume 3 (Reliability Architecture); Volume 7 Ch20 (Business Continuity)
**Tool:** Chaos Mesh or manual Kubernetes disruption

---

## Report Metadata

| Field | Value |
|---|---|
| Date | _(to be filled after Phase 2 execution)_ |
| Environment | Production infrastructure (staging/production alpha, real GPU + CPU nodes) |
| Injection method | Chaos Mesh or manual `kubectl` disruption |
| Scenario scripts | `tests/chaos/chaos-scenarios.py` |
| Status | **PENDING PHASE 2 EXECUTION** |
| Executed by | TBD |
| Report author | TBD |

---

## Methodology

Per Sprint-028 §3, five chaos scenarios are injected against a running system with active calls, and the observed behavior is compared against the expected recovery gate for each scenario. Each scenario is run independently, with the system restored to a healthy baseline between runs.

1. Kill GPU node-0 during 200 concurrent calls → assert graceful failover to GPU node-1, ≤ 5 calls dropped.
2. Kill Redis primary during calls → assert degraded-mode continuation (no data loss, calls complete with possible extra latency).
3. 20% packet loss on RTP path → assert PLC (Packet Loss Concealment) compensates, STT accuracy within 5% of baseline.
4. Kill Postgres primary → assert crash recovery, standby promotes, no PTP duplication (as defined in Volume 3 event-sourcing/idempotency architecture).
5. Kill conversation-engine pod during active call → assert session state recovered from Redis snapshot.

This report captures the template/shell for that execution. Chaos scenarios require real production/staging infrastructure and are a Phase 2-only activity; Phase 1 only produces and syntactically validates the chaos injection scripts.

---

## Results

| # | Scenario | Expected Gate | Observed Result | Pass/Fail |
|---|---|---|---|---|
| 1 | Kill GPU node-0 during 200 concurrent calls | Graceful failover to GPU node-1; ≤ 5 calls dropped | TBD | TBD |
| 2 | Kill Redis primary during calls | Degraded-mode continuation; no data loss; calls complete (possible extra latency) | TBD | TBD |
| 3 | 20% packet loss on RTP path | PLC compensates; STT accuracy within 5% of baseline | TBD | TBD |
| 4 | Kill Postgres primary | Crash recovery; standby promotes; no PTP duplication | TBD | TBD |
| 5 | Kill conversation-engine pod during active call | Session state recovered from Redis snapshot | TBD | TBD |

---

## Detailed Scenario Notes

### Scenario 1 — GPU node-0 kill

- Calls in flight at injection time: TBD
- Calls dropped: TBD (gate: ≤ 5)
- Failover time to GPU node-1: TBD
- _(to be filled after Phase 2 execution)_

### Scenario 2 — Redis primary kill

- Data loss observed: TBD (gate: none)
- Additional latency observed during degraded mode: TBD
- Recovery time to primary restoration: TBD
- _(to be filled after Phase 2 execution)_

### Scenario 3 — 20% RTP packet loss

- Baseline STT accuracy (WER): TBD
- STT accuracy under 20% packet loss: TBD
- Delta: TBD (gate: within 5% of baseline)
- _(to be filled after Phase 2 execution)_

### Scenario 4 — Postgres primary kill

- Standby promotion time: TBD
- Duplicate PTP records observed: TBD (gate: none)
- _(to be filled after Phase 2 execution)_

### Scenario 5 — conversation-engine pod kill

- Session state recovery source: Redis snapshot (expected)
- Recovery time: TBD
- Call continuity observed: TBD
- _(to be filled after Phase 2 execution)_

---

## Acceptance Criteria

- [ ] GPU failure → ≤ 5 calls dropped, graceful failover to node-1
- [ ] Redis failure → calls continue in degraded mode, no data loss
- [ ] RTP 20% packet loss → PLC compensates, STT accuracy within 5% of baseline
- [ ] Postgres failure → crash recovery, standby promotes, no PTP duplication
- [ ] Conversation-engine pod kill → session state recovered from Redis snapshot
- [ ] All 5 chaos scenarios pass their gates

---

## Sign-off

| Role | Name | Date | Signature/Approval |
|---|---|---|---|
| Test Executor | TBD | TBD | PENDING |
| Engineering Lead | TBD | TBD | PENDING |
| Production Readiness Owner | TBD | TBD | PENDING |

**Overall Status:** PENDING PHASE 2 EXECUTION — do not proceed to canary deploy until all 5 scenarios pass their gates.
