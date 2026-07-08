# VoiceOS v2 — Sprint Sequencing Graph

**Generated:** 2026-06-29 · **Updated:** 2026-06-29 (3 new sprints added: 032, 033, 034)
**Purpose:** Visual sprint sequencing map showing the critical path and parallelization opportunities.

---

## Complete Sprint Sequence

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                 EPIC E1 — FOUNDATION                    │
                    │                                                          │
                    │  Sprint-001 ──► Sprint-002 ──► Sprint-003               │
                    │  (Contracts)    (Models)       (Tooling+CI)              │
                    └────────────────────────┬────────────────────────────────┘
                                             │
                    ┌────────────────────────▼────────────────────────────────┐
                    │            TRACKS THAT OPEN AFTER SPRINT-003            │
                    │                                                          │
                    │   Track A         Track B              Track C          │
                    │  (Media)         (Reliability)          (GPU)           │
                    │                                                          │
                    │ 004 Media GW    013 Event Bus         008 GPU Sched     │
                    │ 005 Audio SM    014 Storage                              │
                    │ 006 Preproc     015 State+Recovery                      │
                    │ 007 VAD         016 Concur+Obs                          │
                    └──────────────────────────────────┬──────────────────────┘
                                                       │ (Tracks A,B,C converge)
                    ┌──────────────────────────────────▼──────────────────────┐
                    │         EPIC E3 — CONVERSATION INTELLIGENCE             │
                    │                                                          │
                    │  Sprint-009 (STT+LLM+TTS)                               │
                    │      ↓                                                   │
                    │  Sprint-010 (Perception Engines)                        │
                    │      ↓                                                   │
                    │  Sprint-011 (Decision Engines)                          │
                    │      ↓                                                   │
                    │  Sprint-012 (Orchestration — WALKING SKELETON ✓)        │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    ┌──────────────────────────▼──────────────────────────────┐
                    │               TRACKS THAT OPEN AFTER E3                 │
                    │                                                          │
                    │  Track D (E5 Compliance, deps on E4 reliability)        │
                    │  017 Policy Engine                                       │
                    │  018 Auth+RBAC+AI Gov                                   │
                    │  019 Secrets+Encryption+Privacy                         │
                    │  020 PII+Audit+Security                                 │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    ┌──────────────────────────▼──────────────────────────────┐
                    │           EPIC E6 — SAAS PLATFORM                       │
                    │                                                          │
                    │  Sprint-021 (Multi-Tenancy+Users)                       │
                    │      ↓                                                   │
                    │  Sprint-022 (CRM+Collections)                           │
                    │      ↓                                                   │
                    │  Sprint-023 (Campaigns+Contact Center)                  │
                    │      ↓                ↓                                 │
                    │  Sprint-024 (Billing) │                                  │
                    │      ↓                │                                 │
                    │  Sprint-025 (Admin+API+Integrations) ◄──────────────────┘
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    ┌──────────────────────────▼──────────────────────────────┐
                    │           EPIC E7 — PRODUCTION ALPHA                    │
                    │                                                          │
                    │  Sprint-026 (IaC + Kubernetes)                          │
                    │      ↓                                                   │
                    │  Sprint-027 (Monitoring + DR)                           │
                    │      ↓                                                   │
                    │  Sprint-028 (Perf + Load + PenTest + Alpha Deploy)      │
                    └──────────────────────────┬──────────────────────────────┘
                                               │
                    ┌──────────────────────────▼──────────────────────────────┐
                    │         EPIC E8 — FOUNDER VALIDATION → RELEASE          │
                    │                                                          │
                    │  Sprint-029 (Founder Validation)                        │
                    │      ↓                       ↓                          │
                    │  Sprint-030 (Pilot)    Sprint-034 (Learning Layer)      │
                    │      ↓                       ↓                          │
                    │      └──────────────────────►│                          │
                    └─────────────────────────────┬┴─────────────────────────┘
                                                  │
                    ┌─────────────────────────────▼──────────────────────────┐
                    │      EPIC E6-EXT — ENTERPRISE PLATFORM & ADVANCED SAAS │
                    │  (starts after Sprint-025+026, runs in parallel with E8)│
                    │                                                          │
                    │  Sprint-032 (Enterprise Platform: SSO/SCIM/Multi-Region)│
                    │      ↓                                                   │
                    │  Sprint-033 (Workflow Automation + Customer Success)     │
                    └─────────────────────────────┬────────────────────────── ┘
                                                  │
                    ┌─────────────────────────────▼──────────────────────────┐
                    │      CONVERGENCE — ALL TRACKS MERGE HERE               │
                    │                                                          │
                    │  Sprint-031 (Production Release ✓)                      │
                    │  ← waits for: Sprint-030 + Sprint-032 + Sprint-033      │
                    │                            + Sprint-034                  │
                    └────────────────────────────────────────────────────────┘
```

---

## Critical Path

The critical path (longest dependency chain, blocking production release):

```
001 → 002 → 003 → 004 → 005 → 006 → 007
                                        ↓ (joins 008→009)
              003 → 008 → 009 → 010 → 011 → 012
                                               ↓ (joins reliability: 013→014→015→016)
              003 → 013 → 014 → 015 → 016
                                        ↓
                         014 → 017 → 018 → 019 → 020
                                               ↓
                                  018 → 021 → 022 → 023 → 025
                                               ↓
                                       022 → 024 → 025
                                               ↓
                                       025 → 026 → 027 → 028 → 029 → 030 → 031
```

**Longest chain:** 001→002→003→008→009→010→011→012 + 003→013→014→015→016→017→018→019→020→021→022→023→025→026→027→028→029→030→[032→033]→031

Note: Sprint-034 is also on the critical path via 029→034→031, but Sprint-032→033 is the longer parallel track.

---

## Milestones on the Critical Path

| Milestone | Critical Path Sprint |
|---|---|
| Foundation Complete | Sprint-003 |
| Core Runtime Complete | Sprint-008 (parallel path) + Sprint-007 (media path) |
| Walking Skeleton | Sprint-012 |
| Reliability Complete | Sprint-016 |
| Compliance & Security Complete | Sprint-020 |
| SaaS Platform Complete | Sprint-025 |
| Production Alpha | Sprint-028 |
| Founder Validation | Sprint-029 |
| Pilot Deployment | Sprint-030 |
| Production Release | Sprint-031 |

---

## Parallelization Map

| After Sprint | Can Run In Parallel |
|---|---|
| Sprint-003 complete | Sprint-004 (media track), Sprint-008 (GPU track), Sprint-013 (event bus track), Sprint-014 (storage track) |
| Sprint-002 complete | Sprint-014 can begin (schemas need models but not tooling) |
| Sprint-016 complete (reliability) and Sprint-012 complete (walking skeleton) | Sprint-017 (policy engine) can begin |
| Sprint-023 and Sprint-024 can run in parallel | Both depend on Sprint-022 but not on each other |
| Sprint-026 complete | Sprint-027 and Sprint-028 (partial: infra validation) can begin |
| Sprint-025 + Sprint-026 complete | Sprint-032 (Enterprise Platform) can begin — runs in parallel with Sprint-027/028/029/030 |
| Sprint-032 complete | Sprint-033 (Workflow + Customer Success) can begin |
| Sprint-029 complete | Sprint-034 (Learning Layer) can begin — runs in parallel with Sprint-030/032/033 |
