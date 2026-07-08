# VoiceOS v2 — Canonical Build Order

**Generated:** 2026-06-29 · **Updated:** 2026-06-29 (Tiers 7A and 8A added for new sprints 032–034)
**Purpose:** Defines the exact order in which components are built, grouped by dependency tier.

---

## Tier 0 — Shared Foundation (must exist before any service)

These artifacts have no runtime dependencies and must be built first.

```
src/libs/contracts/          ← ResponsePlan, DecisionEnvelope, EventEnvelope,
                               TurnInput, CustomerContext, AudioFrame, DomainEvent,
                               all domain entities (Customer, LoanAccount, PTP, ...)
src/libs/invariants/         ← RI-1 through RI-8 runtime guard functions
```

**Sprint:** 001, 002

---

## Tier 1 — Infrastructure Clients (depend only on Tier 0)

These are shared library clients used by Tier 2+ services. They have no dependency on each other.

```
src/libs/event-bus/          ← EventBus, Publisher, Consumer, DLQHandler
src/libs/redis-client/       ← RedisClient, DistributedLock, RateLimiter
src/libs/secrets/            ← SecretsManager (vault integration)
src/libs/encryption/         ← EncryptionService
src/libs/pii/                ← PIIDetector, PIIRedactor, PIITokenizer
src/libs/observability/      ← PrometheusClient, StructuredLogger, OTelTracer
src/libs/idempotency/        ← IdempotencyGuard
src/libs/circuit-breaker/    ← CircuitBreaker
src/libs/concurrency/        ← WorkerPool, BoundedQueue
src/libs/state/              ← Snapshot, EventTailReplay, Recoverable
src/libs/health/             ← HealthCheck, ReadinessProbe, LivenessProbe
src/libs/service-discovery/  ← ServiceRegistry, EndpointResolver
src/libs/audit/              ← AuditLogger, AuditEvent
src/libs/privacy/            ← PrivacyEngine, DataErasureJob
```

**Sprints:** 003, 013, 014, 015, 016, 019, 020

---

## Tier 2 — Infrastructure Services (depend on Tier 0 + Tier 1)

```
src/services/gpu-scheduler/  ← GPUScheduler, VRAMledger, ModelPool
src/services/policy-engine/  ← PolicyEngine, PolicyDecision, RBI/DPDP rules
src/services/auth/           ← JWTValidator, OIDCProvider, mTLS enforcer, APIKeyValidator
src/services/authz/          ← RBACEngine, ABACEvaluator, JIT grants
src/services/ai-governance/  ← GovernanceLayer, GovernanceVerdict, LawOfAuthorityChecker
```

**Sprints:** 008, 017, 018

---

## Tier 3 — Media Pipeline (depend on Tier 0 + Tier 1 + Tier 2)

These are the real-time audio processing services. Build in strict pipeline order.

```
src/services/media-gateway/          ← TransportAdapter, TwilioAdapter, SIPAdapter
    ↓
src/services/audio-session-manager/  ← AudioSession, JitterBuffer, PLC, SessionClock
    ↓
src/services/audio-preprocessing/    ← AudioPreprocessor, AEC3, NS, AGC, Resampler
    ↓
src/services/vad-endpointing/        ← VADEngine, EndpointDetector, BargeinDetector
```

**Sprints:** 004, 005, 006, 007

---

## Tier 4 — AI Service Adapters (depend on Tier 2 GPU Scheduler)

These are the GPU-backed AI service adapters. Each wraps a specific model behind a replaceable interface.

```
src/services/stt/            ← STTAdapter (protocol) + WhisperAdapter
src/services/llm-runtime/    ← LLMAdapter (protocol) + vLLMAdapter
src/services/tts/            ← TTSAdapter (protocol) + VeenaAdapter
```

**Sprint:** 009

---

## Tier 5 — Intelligence Engines (depend on Tier 0 + Tier 4 for input contracts)

These engines process TurnInput and produce structured intelligence signals. Each is independently deployable.

```
src/engines/intent/             ← IntentEngine (13 labels, <30ms)
src/engines/entity-extraction/  ← EntityExtractor (slots: amounts, dates, account refs)
src/engines/emotion/            ← EmotionIntelligenceEngine
src/engines/memory/working/     ← WorkingMemoryStore (Redis-backed, per-call)
src/engines/memory/relationship/ ← RelationshipMemoryStore (Postgres-backed, per-customer)
src/engines/conversation-state/ ← ConversationStateIntelligence
    ↓ (perception output feeds decision engines)
src/engines/risk/               ← RiskEngine
src/engines/dialogue-policy/    ← DialoguePolicyEngine
src/engines/strategy/           ← StrategyEngine
src/engines/goal-planner/       ← GoalPlanner
src/engines/negotiation/        ← NegotiationEngine (non-bypassable clamp)
src/engines/empathy/            ← EmpathyPlanner
    ↓ (decision output feeds assembly)
src/engines/response-planning/  ← ResponsePlanningEngine (assembles final ResponsePlan)
src/engines/output-evaluation/  ← OutputEvaluationEngine (post-turn quality scoring)
```

**Sprints:** 010, 011

---

## Tier 6 — Conversation Orchestration (depend on Tier 3 + Tier 4 + Tier 5)

The orchestration layer that coordinates all engines and services into a real call.

```
src/engines/prompt-builder/      ← PromptBuilder (deterministic, versioned, RI-7)
src/services/dialogue-manager/   ← DialogueManager (turn coordination, barge-in)
src/services/conversation-engine/ ← ConversationEngine (full CIL orchestration)
src/services/llm-runtime/output_validator.py ← OutputValidator (RI-5, Law of Authority)
src/services/tts/streaming_pipeline.py ← TrueStreamingPipeline (end-to-end streaming)
src/services/playback/           ← PlaybackScheduler, AudioOutput
```

**Sprint:** 012

---

## Tier 7 — SaaS Services (depend on Tier 1 + Tier 2 Compliance)

```
src/services/tenant-management/  ← TenantService, TenantLifecycle
src/services/user-management/    ← UserService, RoleAssignment
    ↓
src/services/crm/                ← CustomerService, PartyRepository, CustomerContextAssembler
src/services/collections/        ← LoanAccountService, PTPs, SettlementService
    ↓
src/services/campaign-management/ ← CampaignService, AudienceSelector, ScheduleEngine
src/services/contact-center/     ← ContactCenterService, SkillsBasedRouter, LiveTransfer
src/services/hitl/               ← HITLQueue, SLAEnforcer, HumanReviewAPI, OverrideLogger
    ↓
src/services/billing/            ← BillingService, SubscriptionManager
src/services/metering/           ← MeteringService, UsageAggregator
src/services/analytics/          ← AnalyticsService, CallAnalytics, CampaignAnalytics
src/services/reporting/          ← ReportingService, ExportService
src/services/bi-platform/        ← BIWarehouse, ForecastingEngine, CrossTenantBenchmarking
    ↓
src/services/admin-portal/       ← Admin API backend
src/services/ai-config/          ← PromptVersioningService, ModelConfigService
src/services/integration-platform/ ← WebhookService, WebhookDeliveryEngine
src/services/api-platform/       ← Public REST API (OpenAPI 3.1)
src/services/saas-ops/           ← FeatureFlagService, FleetRolloutManager, TenantDataMigration
```

**Sprints:** 021, 022, 023, 024, 025, 026 (saas-ops)

---

## Tier 7A — Enterprise & Advanced SaaS (depend on Tier 7 + Tier 8)

```
src/services/enterprise-platform/  ← SAMLProvider, OIDCProvider, SCIMService, AuditExportPipeline,
                                       DataResidencyEnforcer, ClusterProvisioningService
    ↓
src/services/workflow-engine/       ← WorkflowEngine, WorkflowExecutor, TriggerEvaluator, ActionDispatcher
src/services/customer-success/      ← OnboardingTracker, TenantHealthScorer, ChurnPredictor, NPSCollector
```

**Sprints:** 032, 033

---

## Tier 5A — Learning Layer (depend on Tier 5 intelligence engines + production data from Sprint-029)

```
src/services/learning-layer/        ← FailedTurnMiner, WeakLabelGenerator, ModelDriftTracker,
                                       OfflineTrainingPipeline, ModelPromoter, PromotionGate
```

**Sprint:** 034

---

## Tier 8 — Infrastructure & Operations (depend on all service tiers being stable)

```
infra/terraform/    ← VPC, instances, managed DBs, KMS, storage
infra/helm/         ← Helm charts for all services
infra/k8s/          ← Node pools, network policies, PDBs, HPA configs
```

**Sprint:** 026

---

## Tier 9 — Observability Stack (depend on Tier 8 infrastructure)

```
monitoring/prometheus/   ← Scrape configs, alert rules
monitoring/grafana/      ← Dashboard definitions (SLO, error-budget, GPU, call funnel)
monitoring/alertmanager/ ← Routing, escalation
logging/                 ← Log aggregation pipeline
tracing/                 ← OpenTelemetry collector, Jaeger/Tempo config
```

**Sprint:** 027

---

## Build Order Summary (strict sequence)

```
Tier 0 → Tier 1 → Tier 2 → Tier 3 → Tier 4 → Tier 5 → Tier 6
                                                              ↓
                                              Tier 7 (parallel with Tier 6 post-012)
                                                              ↓
                                                           Tier 8
                                                              ↓
                                                           Tier 9
                                                              ↓
                                   Tier 7A (parallel with Tier 9 post-026) ─┐
                                   Tier 5A (parallel with Tier 7A post-029) ─┤
                                                                             ↓
                                                           Production Release (Sprint-031)
```

**Key rule:** Never build a component that depends on an unbuilt tier. Violating build order creates circular dependencies and unmockable test failures.

**Tiers 7A and 5A** can run in parallel with each other and with E8 (Sprint-029/030) after their declared dependencies are met. All three must complete before Sprint-031.
