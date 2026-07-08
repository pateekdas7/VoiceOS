# VoiceOS v2 — Component & Sprint Dependency Graph

**Generated:** 2026-06-29 · **Updated:** 2026-06-29 (3 new sprints: 032, 033, 034)
**Purpose:** Reference for all implementation dependencies. Before starting any sprint, all listed dependencies must be ✅ complete.

---

## Sprint Dependencies

```
Sprint-001 ─────────────────────────────────────────────────────────────► Sprint-002
    │                                                                           │
    └───────────────────────────────────────────────────────────────────────► Sprint-003
                                                                                │
    Sprint-001 ─► Sprint-008 (GPU Scheduler, no media deps)                    │
    Sprint-001 ─► Sprint-013 (Event Bus, no media deps)                        │
    Sprint-002 ─► Sprint-014 (Persistent Storage, needs models)                │
                                                                                ▼
Sprint-003 ─────────────────────────────────────────────────────────────► Sprint-004
                                                                                │
                                                                                ▼
                                                                          Sprint-005
                                                                                │
                                                                                ▼
                                                                          Sprint-006
                                                                                │
                                                                                ▼
                                                                          Sprint-007

Sprint-008 ─────────────────────────────────────────────────────────────► Sprint-009
                                                                                │
                                              Sprint-001 ─────────────────►    │
                                              Sprint-002 ─────────────────►    ▼
                                                                          Sprint-010
                                                                                │
                                                                                ▼
                                                                          Sprint-011
                                                                                │
Sprint-009 ──────────────────────────────────────────────────────────────►     │
Sprint-010 ──────────────────────────────────────────────────────────────►     ▼
Sprint-011 ──────────────────────────────────────────────────────────────► Sprint-012

Sprint-013 ──────────────────────────────────────────────────────────────► Sprint-015
Sprint-014 ──────────────────────────────────────────────────────────────► Sprint-015
Sprint-015 ──────────────────────────────────────────────────────────────► Sprint-016
Sprint-013 ──────────────────────────────────────────────────────────────► Sprint-016
Sprint-014 ──────────────────────────────────────────────────────────────► Sprint-016

Sprint-001 ──────────────────────────────────────────────────────────────► Sprint-017
Sprint-002 ──────────────────────────────────────────────────────────────► Sprint-017
Sprint-013 ──────────────────────────────────────────────────────────────► Sprint-017
Sprint-014 ──────────────────────────────────────────────────────────────► Sprint-017
Sprint-017 ──────────────────────────────────────────────────────────────► Sprint-018
Sprint-017 ──────────────────────────────────────────────────────────────► Sprint-019
Sprint-018 ──────────────────────────────────────────────────────────────► Sprint-019
Sprint-018 ──────────────────────────────────────────────────────────────► Sprint-020
Sprint-019 ──────────────────────────────────────────────────────────────► Sprint-020

Sprint-018 ──────────────────────────────────────────────────────────────► Sprint-021
Sprint-020 ──────────────────────────────────────────────────────────────► Sprint-021
Sprint-021 ──────────────────────────────────────────────────────────────► Sprint-022
Sprint-014 ──────────────────────────────────────────────────────────────► Sprint-022
Sprint-015 ──────────────────────────────────────────────────────────────► Sprint-022
Sprint-022 ──────────────────────────────────────────────────────────────► Sprint-023
Sprint-021 ──────────────────────────────────────────────────────────────► Sprint-024
Sprint-022 ──────────────────────────────────────────────────────────────► Sprint-024
Sprint-021 ──────────────────────────────────────────────────────────────► Sprint-025
Sprint-023 ──────────────────────────────────────────────────────────────► Sprint-025
Sprint-024 ──────────────────────────────────────────────────────────────► Sprint-025

Sprint-016 ──────────────────────────────────────────────────────────────► Sprint-026
Sprint-020 ──────────────────────────────────────────────────────────────► Sprint-026
Sprint-025 ──────────────────────────────────────────────────────────────► Sprint-026
Sprint-026 ──────────────────────────────────────────────────────────────► Sprint-027
Sprint-026 ──────────────────────────────────────────────────────────────► Sprint-028
Sprint-027 ──────────────────────────────────────────────────────────────► Sprint-028
Sprint-022 ──────────────────────────────────────────────────────────────► Sprint-028
Sprint-023 ──────────────────────────────────────────────────────────────► Sprint-028

Sprint-028 ──────────────────────────────────────────────────────────────► Sprint-029
Sprint-029 ──────────────────────────────────────────────────────────────► Sprint-030
Sprint-029 ──────────────────────────────────────────────────────────────► Sprint-034
Sprint-030 ──────────────────────────────────────────────────────────────► Sprint-031
Sprint-032 ──────────────────────────────────────────────────────────────► Sprint-031
Sprint-033 ──────────────────────────────────────────────────────────────► Sprint-031
Sprint-034 ──────────────────────────────────────────────────────────────► Sprint-031

Sprint-025 ──────────────────────────────────────────────────────────────► Sprint-032
Sprint-021 ──────────────────────────────────────────────────────────────► Sprint-032
Sprint-018 ──────────────────────────────────────────────────────────────► Sprint-032
Sprint-019 ──────────────────────────────────────────────────────────────► Sprint-032
Sprint-020 ──────────────────────────────────────────────────────────────► Sprint-032
Sprint-026 ──────────────────────────────────────────────────────────────► Sprint-032

Sprint-025 ──────────────────────────────────────────────────────────────► Sprint-033
Sprint-013 ──────────────────────────────────────────────────────────────► Sprint-033
Sprint-022 ──────────────────────────────────────────────────────────────► Sprint-033
Sprint-023 ──────────────────────────────────────────────────────────────► Sprint-033
Sprint-024 ──────────────────────────────────────────────────────────────► Sprint-033
Sprint-032 ──────────────────────────────────────────────────────────────► Sprint-033

Sprint-010 ──────────────────────────────────────────────────────────────► Sprint-034
Sprint-012 ──────────────────────────────────────────────────────────────► Sprint-034
Sprint-029 ──────────────────────────────────────────────────────────────► Sprint-034
```

---

## Component Dependencies (Key Cross-Cutting Contracts)

### `ResponsePlan` (defined Sprint-001)
Used by: ConversationEngine, PromptBuilder, OutputValidator, ResponsePlanningEngine, AI Governance, DecisionEnvelope, MongoDB storage, Admin analytics

### `DecisionEnvelope` (defined Sprint-001)
Used by: ConversationEngine, AI Governance, Audit, MongoDB lineage storage, Supervisor context handoff

### `EventEnvelope / DomainEvent` (defined Sprint-001, expanded Sprint-002)
Used by: EventBus, all event publishers/consumers, Idempotency dedup, Event Sourcing replay

### `CustomerContext` (defined Sprint-001, assembled Sprint-022)
Used by: ConversationEngine, PromptBuilder, CIL engines, AI Governance, Audit

### `TurnInput` (defined Sprint-001)
Used by: IntentEngine, EntityExtractor, EmotionEngine, DialogueManager, ConversationEngine

### `AudioFrame` (defined Sprint-001)
Used by: MediaGateway, AudioSessionManager, Preprocessing, VAD, STT

### `IdempotencyGuard` (implemented Sprint-015)
Used by: PTPs (Sprint-022), all authoritative write operations

### `PolicyEngine` (implemented Sprint-017)
Used by: Auth (Sprint-018), Collections workflows (Sprint-022), Campaign scheduling (Sprint-023), Billing entitlements (Sprint-024)

### `GovernanceVerdict` / AI Governance (implemented Sprint-018)
Used by: ConversationEngine (Sprint-012), OutputValidator (Sprint-012)

---

## Parallelization Opportunities

Once Sprint-003 is complete and contracts are frozen, the following tracks can proceed in parallel:

| Track A | Track B | Track C |
|---|---|---|
| E2 (media pipeline): 004→005→006→007 | E4 (reliability): 013→014→015→016 | GPU Scheduler: Sprint-008 |

Once Sprint-012 walking skeleton is stable and Sprint-016 + Sprint-020 are complete:

| Track D | — |
|---|---|
| E6 (SaaS): 021→022→023→024→025 | Can run while E7 Sprint-026 is being planned |

**Important:** Track parallelization requires the shared contracts in Tier 0 to be fully stable and passing mypy --strict. Do not parallelize before Sprint-002 is complete.
