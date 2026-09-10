"""VoiceOS CPU Node — Composition Root (Path-A consolidation, Phase 2).

Constructs a fully-wired ConversationEngine against this node's REAL
Postgres/Redis and the GPU node's REAL STT/LLM/TTS inference servers.

Why this file exists: an architecture audit (2026-07-25) found no
composition root anywhere in this repo — every prior "real" wiring
(scripts/validate/walking_skeleton.py::build_real_engine) used a stub GPU
scheduler and skipped Postgres/Redis/EventBus/PolicyEngine entirely, and
the actual founder-approved calls ran through a completely separate,
duplicate-logic script (evaluation/founder-validation/conv_server.py) that
never touched src/services/ or src/engines/response_planning at all. This
file is the single place every real dependency is constructed exactly
once, per the approved consolidation plan's Decision #5.

Boundary note: this file lives under deployment/, not src/ — it is
exempt from check_boundaries.py's Rule 1/2 (which only scans src/) by the
same precedent as scripts/validate/walking_skeleton.py, which already
constructs both src/engines/ classes (the CIL) and src/services/ classes
(ConversationEngine) side by side outside of src/.

Phase 2 scope: construction only. build_conversation_engine() returns a
working ConversationEngine that can run handle_turn() (as
walking_skeleton.py already does with hand-built TurnInputs).

Phase 4 addition: build_shared_call_dependencies() constructs everything
src/services/media_gateway/twilio_ws_entrypoint.py's Starlette app needs
(MediaGatewayService, AudioSessionManagerService, AudioPreprocessorService,
the real GPU-backed STT via WhisperHTTPAdapter, and the same
ConversationEngine build_conversation_engine() produces) — serve() binds
this to a real uvicorn process, making this composition root's output
reachable by a real Twilio phone call for the first time.

Phase 5 addition: build_conversation_engine() now wires a
PromiseToPayService into ConversationEngine — a finalized negotiation
commitment (ACCEPT/PROPOSE_PTP) is persisted to Collections, not just
computed and spoken.

Usage:
    python deployment/cpu/app.py --smoke-test
        Constructs the full dependency graph and verifies every
        constructor succeeds against the real environment described by
        .env (see deployment/cpu/.env.example for every variable read
        here). Exits non-zero on any construction failure.

    python deployment/cpu/app.py --serve [--port 8010]
        Binds the real Twilio Media Streams WebSocket entrypoint
        (WS /twilio/media-stream) via uvicorn.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("voiceos.cpu_app")


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


# ---------------------------------------------------------------------------
# Postgres
# ---------------------------------------------------------------------------


def build_postgres_connection() -> object:
    """Open a real psycopg2 connection.

    Prefers POSTGRES_DSN (the pattern every scripts/sprintNNN_infra_validation.py
    script already uses — operator-supplied at invocation time, e.g. from a
    Vault CLI read) over the individual POSTGRES_HOST/PORT/DB/USER/PASSWORD
    variables in deployment/cpu/.env.example. Neither this file nor .env
    ever hardcodes a real password — .env.example's own comment documents
    that Postgres credentials are Vault-fetched at runtime in production;
    this composition root does not yet do that fetch itself (follow-up,
    not blocking Phase 2's construction-only scope) and instead requires
    the caller to supply one of the two forms above.

    A single shared connection is used for every repository in this
    composition root (BaseRepository's conn contract just needs
    .cursor()/.commit() — see src/libs/repositories/base.py)."""
    import psycopg2

    dsn = _env("POSTGRES_DSN")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=_env("POSTGRES_HOST", "127.0.0.1"),
        port=int(_env("POSTGRES_PORT", "5432")),
        dbname=_env("POSTGRES_DB", "voiceos"),
        user=_env("POSTGRES_USER", "voiceos"),
        password=_env("POSTGRES_PASSWORD", required=True),
    )


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


def _redis_url() -> str:
    """Same POSTGRES_DSN-style override pattern: prefer an operator-supplied
    REDIS_URL, else assemble from REDIS_HOST/PORT/PASSWORD."""
    url = _env("REDIS_URL")
    if url:
        return url
    host = _env("REDIS_HOST", "127.0.0.1")
    port = _env("REDIS_PORT", "6379")
    password = _env("REDIS_PASSWORD", "")
    auth = f":{password}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


def build_raw_redis_client() -> object:
    """A raw redis.Redis client — the type every Any-typed 'redis' param in
    this codebase (EventBus, PolicyEngine) actually expects. Deliberately
    NOT the src.libs.redis_client.RedisClient wrapper here — mixing the two
    was a real bug found during Sprint-021 (a RedisClient-wrapper-vs-raw-
    client mismatch in a validation script); this composition root uses the
    raw client uniformly everywhere an Any-typed redis param appears."""
    import redis

    return redis.Redis.from_url(_redis_url())


# ---------------------------------------------------------------------------
# GPU Scheduler (singleton VRAM ledger shared by STT/LLM/TTS adapters)
# ---------------------------------------------------------------------------


def build_gpu_scheduler() -> object:
    from src.services.gpu_scheduler.scheduler import GPUScheduler
    from src.services.gpu_scheduler.vram_ledger import VRAMLedger

    ledger = VRAMLedger()
    ledger.register_device("gpu0", int(_env("GPU_VRAM_MB", "49140")))  # RTX A6000, see GPU_NODE_STATE.md
    return GPUScheduler(ledger)


# ---------------------------------------------------------------------------
# CRM / Collections repositories + services
# ---------------------------------------------------------------------------


def build_crm_repositories(conn: object) -> object:
    from src.libs.repositories.customer import CustomerRepository
    from src.libs.repositories.party import PartyRepository
    from src.services.crm.repository import CRMRepositories

    return CRMRepositories(customer=CustomerRepository(conn), party=PartyRepository(conn))


def build_customer_service(conn: object) -> object:
    """Single authoritative CustomerService instance. Shared between
    build_customer_context_assembler() (used by ConversationEngine) and
    build_shared_call_dependencies() (used by the /voice HTTP handler for
    ANI-based customer_id resolution) so both paths see the same source
    of truth."""
    from src.services.crm.service import CustomerService
    return CustomerService(repositories=build_crm_repositories(conn))


def build_customer_context_assembler(
    conn: object, customer_service: object | None = None
) -> object:
    from src.libs.repositories.consent import ConsentRepository
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.loan_account import LoanAccountRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.loan_account import LoanAccountService
    from src.services.crm.context_assembler import CustomerContextAssembler
    from src.services.crm.party import PartyService

    crm_repos = build_crm_repositories(conn)
    if customer_service is None:
        customer_service = build_customer_service(conn)
    party_service = PartyService(repository=crm_repos.party)
    emi_service = EMIScheduleService(repository=EMIScheduleRepository(conn))
    loan_service = LoanAccountService(repository=LoanAccountRepository(conn), emi_schedule_service=emi_service)

    return CustomerContextAssembler(
        customer_service=customer_service,
        party_service=party_service,
        loan_account_service=loan_service,
        emi_schedule_service=emi_service,
        consent_repository=ConsentRepository(conn),
    )


def build_promise_to_pay_service(
    conn: object,
    idempotency_guard: object,
    policy_engine_service: object,
) -> object:
    """Not yet called from ConversationEngine's turn-handling path — that's
    Phase 5. Built here so Phase 5 only has to wire a call site, not also
    invent this construction."""
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.promise_to_pay import PromiseToPayRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.promise_to_pay import PromiseToPayService

    return PromiseToPayService(
        repository=PromiseToPayRepository(conn),
        emi_schedule_service=EMIScheduleService(repository=EMIScheduleRepository(conn)),
        idempotency_guard=idempotency_guard,
        policy_engine_service=policy_engine_service,
    )


# ---------------------------------------------------------------------------
# Policy Engine / Idempotency / Event Bus
# ---------------------------------------------------------------------------


def build_policy_engine_service(conn: object, raw_redis: object) -> object:
    from src.libs.repositories.policy import PolicyRepository
    from src.services.policy_engine.service import PolicyEngineService

    return PolicyEngineService.create(redis=raw_redis, policy_repository=PolicyRepository(conn))


def build_idempotency_guard(conn: object) -> object:
    from src.libs.idempotency.guard import IdempotencyGuard
    from src.libs.repositories.idempotency import IdempotencyRepository

    return IdempotencyGuard(repository=IdempotencyRepository(conn))


def build_event_bus_adapter(raw_redis: object) -> object:
    from src.libs.event_bus.bus import EventBus
    from src.libs.event_bus.publisher import Publisher
    from src.services.conversation_engine.event_bus_adapter import RedisEventBusAdapter

    stream = _env("EVENT_BUS_STREAM", "voiceos-events")
    max_retries = int(_env("EVENT_BUS_MAX_RETRIES", "3"))
    bus = EventBus(raw_redis, stream=stream, max_retries=max_retries)
    return RedisEventBusAdapter(Publisher(bus))


def build_ai_governance_service(policy_engine_service: object) -> object:
    from src.services.ai_governance.service import AIGovernanceService

    threshold = float(_env("AI_GOVERNANCE_HUMAN_REVIEW_THRESHOLD", "0.9"))
    return AIGovernanceService.create(
        policy_engine_service=policy_engine_service,
        human_review_threshold=threshold,
    )


# ---------------------------------------------------------------------------
# GPU-backed LLM / TTS (real HTTP calls to the GPU node — see
# deployment/GPU_NODE_STATE.md for the currently-deployed host/ports)
# ---------------------------------------------------------------------------


def _gpu_service_url(env_var: str, default_port: int) -> str:
    """Resolve a GPU service base URL.

    Phase-5 integration: Kaggle notebooks have no public IP, so GPU services
    are exposed via Cloudflare Quick Tunnels. LLM_BASE_URL / STT_BASE_URL /
    TTS_BASE_URL let each adapter point at its own tunnel URL rather than
    deriving all three from a single GPU_NODE_HOST:port. Falls back to
    http://{GPU_NODE_HOST}:{default_port} when not set (direct-IP deployments).
    """
    override = _env(env_var)
    if override:
        return override.rstrip("/")
    gpu_host = _env("GPU_NODE_HOST", required=True)
    return f"http://{gpu_host}:{default_port}"


_CIRCUIT_BREAKER_REGISTRY: object = None


def build_circuit_breaker_registry() -> object:
    """Process-wide ``CircuitBreakerRegistry`` for the three GPU-node adapters.

    V3 Ch14 §14.2 mandates one breaker per external dependency and one
    ``on_state_change`` sink; both are satisfied here by wiring
    ``record_circuit_breaker_state`` so every trip/close updates the
    ``voiceos_circuit_breaker_state`` gauge without adapter-side plumbing.
    Cached because ``build_conversation_engine`` (llm/tts) and
    ``build_shared_call_dependencies`` (stt) each build a service tree; both
    must share breakers so the "5 failures in 30s" window sees every call.
    """
    global _CIRCUIT_BREAKER_REGISTRY
    if _CIRCUIT_BREAKER_REGISTRY is None:
        from src.libs.circuit_breaker.breaker import CircuitBreakerRegistry
        from src.libs.observability.metrics import record_circuit_breaker_state

        _CIRCUIT_BREAKER_REGISTRY = CircuitBreakerRegistry(
            on_state_change=record_circuit_breaker_state,
        )
    return _CIRCUIT_BREAKER_REGISTRY


def build_llm_service(gpu_scheduler: object) -> object:
    from src.services.llm_runtime.adapters.vllm_adapter import vLLMAdapter
    from src.services.llm_runtime.prompt_contract import PromptContract
    from src.services.llm_runtime.service import LLMService, LLMServiceConfig

    base_url = _gpu_service_url("LLM_BASE_URL", 8000)
    breaker = build_circuit_breaker_registry().get_or_create("llm")  # type: ignore[attr-defined]
    adapter = vLLMAdapter(
        gpu_scheduler=gpu_scheduler,
        prompt_contract=PromptContract(),
        base_url=base_url,
        breaker=breaker,
    )
    return LLMService.create(adapter=adapter, config=LLMServiceConfig(base_url=base_url))


def build_tts_service(gpu_scheduler: object) -> object:
    from src.services.tts.adapters.veena_adapter import VeenaAdapter
    from src.services.tts.service import TTSService, TTSServiceConfig

    base_url = _gpu_service_url("TTS_BASE_URL", 8200)
    breaker = build_circuit_breaker_registry().get_or_create("tts")  # type: ignore[attr-defined]
    adapter = VeenaAdapter(gpu_scheduler=gpu_scheduler, base_url=base_url, breaker=breaker)
    return TTSService.create(adapter=adapter, config=TTSServiceConfig(base_url=base_url))


# ---------------------------------------------------------------------------
# CIL (Conversation Intelligence Layer) — real engines, zero-arg
# constructors, identical set to scripts/validate/walking_skeleton.py's
# _build_engine(), now driven through the Phase-1-fixed orchestrator.
# ---------------------------------------------------------------------------


def build_cil() -> object:
    from src.engines.adaptive_conversation.engine import AdaptiveConversationEngine
    from src.engines.dialogue_policy.engine import DialoguePolicyEngine
    from src.engines.emotion.engine import EmotionIntelligenceEngine
    from src.engines.empathy.engine import EmpathyPlanner
    from src.engines.entity_extraction.engine import EntityExtractor
    from src.engines.goal_planner.engine import GoalPlanner
    from src.engines.intent.engine import IntentEngine
    from src.engines.intent.model import IntentModel
    from src.engines.negotiation.engine import NegotiationEngine
    from src.engines.response_planning.engine import ResponsePlanningEngine
    from src.engines.risk.engine import RiskEngine
    from src.engines.sales.action_planner import SalesActionPlanner
    from src.engines.sales.domains.real_estate import RealEstateDomainConfig
    from src.engines.sales.question_selector import QuestionSelector
    from src.engines.sales.state_updater import SalesStateUpdater
    from src.engines.strategy.engine import StrategyEngine

    domain_cfg = RealEstateDomainConfig()

    return ResponsePlanningEngine(
        intent_engine=IntentEngine(IntentModel()),
        entity_extractor=EntityExtractor(),
        emotion_engine=EmotionIntelligenceEngine(),
        risk_engine=RiskEngine(),
        dialogue_policy_engine=DialoguePolicyEngine(),
        strategy_engine=StrategyEngine(),
        goal_planner=GoalPlanner(),
        negotiation_engine=NegotiationEngine(),
        empathy_planner=EmpathyPlanner(),
        adaptive_conv_engine=AdaptiveConversationEngine(),
        sales_state_updater=SalesStateUpdater(domain_cfg),
        question_selector=QuestionSelector(domain_cfg),
        sales_action_planner=SalesActionPlanner(domain_cfg),
    )


# ---------------------------------------------------------------------------
# Path-A Phase 6f/6g — the scripted-response golden path
# ---------------------------------------------------------------------------


def build_working_memory_store(raw_redis: object) -> object:
    """V2 Ch11: Redis-backed per-call short-term state (WorkingMemoryStore).

    Stores intent, entity, negotiation, and strategy state per active call
    with a 4-hour TTL. Passed to ConversationEngine so __handle_turn_body
    updates it after every CIL pipeline run — AdaptiveConversationEngine and
    downstream engines can retrieve it on subsequent turns via the CSI tracker.
    """
    from src.engines.memory.working.store import WorkingMemoryStore

    return WorkingMemoryStore(raw_redis)


def build_relationship_memory_store(conn: object) -> object:
    """V2 Ch12: Postgres-backed cross-call per-customer state (RelationshipMemoryStore).

    Persists PTP history, sentiment trend, escalation count, and last outcome
    across calls. Loaded at call start and updated at call end so the next
    call's CIL has this customer's longitudinal context from the first turn.
    """
    from src.engines.memory.relationship.store import RelationshipMemoryStore

    return RelationshipMemoryStore(conn)


def build_callback_scheduler(conn: object) -> object:
    """V5 Ch4.5: CallbackScheduler backed by Postgres CallbackRepository.

    Publishes ``saas.callback.scheduled`` events via the EventBus Publisher
    so downstream systems (dialer, CRM) can react to scheduled callbacks.
    Uses a dedicated connection (clean transaction boundary).
    """
    from src.libs.event_bus.bus import EventBus
    from src.libs.event_bus.publisher import Publisher
    from src.libs.repositories.callback import CallbackRepository
    from src.services.collections.callback import CallbackScheduler

    raw_redis = build_raw_redis_client()
    stream = _env("EVENT_BUS_STREAM", "voiceos-events")
    bus = EventBus(raw_redis, stream=stream, max_retries=3)
    publisher = Publisher(bus)
    return CallbackScheduler(repository=CallbackRepository(conn), publisher=publisher)


def build_call_summary_repository(conn: object) -> object:
    """Phase 3: Postgres-backed post-call sales summary repository.

    Stores structured summaries from generate_post_call_summary() for CRM
    integration, analytics, and supervisor review. Uses ``call_summaries``
    table (created lazily on first use via ensure_table()).
    """
    from src.libs.repositories.call_summary import PostCallSummaryRepository

    repo = PostCallSummaryRepository(conn)
    try:
        repo.ensure_table()
    except Exception:
        logger.warning("PostCallSummaryRepository.ensure_table() failed — table may not exist yet")
    return repo


def build_sales_action_dispatcher(callback_scheduler: object) -> object:
    """Phase 3: SalesProductionActionDispatcher wired with real CallbackScheduler.

    SCHEDULE_FOLLOWUP → CallbackScheduler.schedule()
    HUMAN_HANDOFF → returns signal; ConversationEngine calls escalate_call()
    """
    from src.engines.sales.production_actions import SalesProductionActionDispatcher

    return SalesProductionActionDispatcher(callback_scheduler=callback_scheduler)


def build_dialogue_response_engine() -> object:
    """The deterministic scripted-response FSM (Path-A Phase 6f) that
    drove Call-001 — zero-arg constructor, same pattern as build_cil()'s
    engines: DialogueResponseEngine owns no I/O of its own, it only reads
    ResponsePlan/CustomerContext/ConversationSessionState it's handed."""
    from src.engines.dialogue_response.engine import DialogueResponseEngine

    return DialogueResponseEngine()


# ---------------------------------------------------------------------------
# Top-level: build a fully-wired ConversationEngine
# ---------------------------------------------------------------------------


def build_conversation_engine() -> object:
    """Construct a ConversationEngine with every real dependency wired.

    Returns the engine plus nothing else — callers needing the raw
    Postgres connection (e.g. Phase 4's telephony transport, to build an
    AudioSessionManagerService/DialogueManager alongside this engine) should
    call the individual build_*() functions above directly rather than
    re-deriving them.
    """
    from src.engines.output_evaluation.engine import OutputEvaluationEngine
    from src.engines.prompt_builder.builder import PromptBuilder
    from src.libs.observability.logger import StructuredLogger
    from src.services.conversation_quality.scorer import ConversationQualityScorer
    from src.services.conversation_engine.engine import ConversationEngine
    from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
    from src.services.llm_runtime.output_validator import OutputValidator

    conn = build_postgres_connection()
    raw_redis = build_raw_redis_client()
    gpu_scheduler = build_gpu_scheduler()

    policy_engine_service = build_policy_engine_service(conn, raw_redis)
    idempotency_guard = build_idempotency_guard(conn)

    engine = ConversationEngine(
        cil=build_cil(),
        prompt_builder=PromptBuilder(),
        llm_service=build_llm_service(gpu_scheduler),
        tts_service=build_tts_service(gpu_scheduler),
        validator=OutputValidator(),
        knowledge=KnowledgeRetrievalService(),
        quality_scorer=ConversationQualityScorer(),
        ai_governance_service=build_ai_governance_service(policy_engine_service),
        output_evaluator=OutputEvaluationEngine(),
        event_bus=build_event_bus_adapter(raw_redis),
        idempotency_guard=idempotency_guard,
        policy_engine_service=policy_engine_service,
        context_assembler=build_customer_context_assembler(conn),
        structured_logger=StructuredLogger("voiceos-cpu-app"),
        # Path-A Phase 5: a finalized negotiation commitment (ACCEPT/
        # PROPOSE_PTP) is now durably persisted to Collections — reuses the
        # same idempotency_guard/policy_engine_service instances constructed
        # above, on a fresh Postgres connection (a repository connection
        # should not be shared with one already handed to other repositories
        # constructed on `conn` above, to keep transaction boundaries clean).
        promise_to_pay_service=build_promise_to_pay_service(
            build_postgres_connection(), idempotency_guard, policy_engine_service
        ),
        # Path-A Phase 6g: the scripted golden path becomes the primary
        # reply path for every turn once wired here (LLM path above stays
        # built and available — engine.py falls back to it whenever
        # dialogue_response is None, but on this composition root it never
        # is). LENDER_NAME defaults to the founder-validation trial's own
        # lender ("Rajat Finance") only because no multi-tenant lender
        # registry exists yet — every real tenant must set this explicitly.
        dialogue_response=build_dialogue_response_engine(),
        lender_name=_env("LENDER_NAME", "Rajat Finance"),
        # V2 Ch11/12: memory stores wired so every engine that participates
        # via the CIL pipeline has access to cross-turn and cross-call state.
        # Relationship store uses a dedicated connection (clean transaction
        # boundary — same pattern as promise_to_pay_service above).
        working_memory_store=build_working_memory_store(raw_redis),
        relationship_memory_store=build_relationship_memory_store(build_postgres_connection()),
        # Phase 3: production action wiring — callback scheduling +
        # post-call summary persistence. Each uses a dedicated Postgres
        # connection to keep transaction boundaries clean.
        sales_action_dispatcher=build_sales_action_dispatcher(
            build_callback_scheduler(build_postgres_connection())
        ),
        call_summary_repository=build_call_summary_repository(build_postgres_connection()),
    )
    return engine


# ---------------------------------------------------------------------------
# Phase 4 — Twilio Media Streams WS entrypoint dependencies
# ---------------------------------------------------------------------------


def build_stt_service(gpu_scheduler: object) -> object:
    """Real GPU-backed STT.

    Selects between HTTP one-shot (WhisperHTTPAdapter — POST /transcribe) and
    truly-streaming WebSocket (WhisperStreamingAdapter — WS /transcribe_ws)
    via the VOICEOS_STT_STREAMING env flag. Streaming is the target once the
    GPU-side WS endpoint is validated; HTTP remains the safe fallback."""
    from src.services.stt.service import STTService, STTServiceConfig

    base_url = _gpu_service_url("STT_BASE_URL", 8100)
    breaker = build_circuit_breaker_registry().get_or_create("stt")  # type: ignore[attr-defined]
    streaming = _env("VOICEOS_STT_STREAMING", "0").strip() == "1"
    _stt_logger = logging.getLogger("voiceos.deployment.cpu")
    if streaming:
        from src.services.stt.adapters.whisper_streaming_adapter import WhisperStreamingAdapter
        adapter = WhisperStreamingAdapter(gpu_scheduler=gpu_scheduler, base_url=base_url, breaker=breaker)
        _stt_logger.info("STT adapter=WhisperStreamingAdapter base_url=%s (VOICEOS_STT_STREAMING=1)", base_url)
    else:
        from src.services.stt.adapters.whisper_http_adapter import WhisperHTTPAdapter
        adapter = WhisperHTTPAdapter(gpu_scheduler=gpu_scheduler, base_url=base_url, breaker=breaker)
        _stt_logger.info("STT adapter=WhisperHTTPAdapter base_url=%s (VOICEOS_STT_STREAMING=0)", base_url)
    return STTService.create(adapter=adapter, config=STTServiceConfig(default_language=_env("STT_LANGUAGE", "hi")))


def build_media_gateway_service() -> object:
    from src.services.media_gateway.service import MediaGatewayService

    return MediaGatewayService()


def build_audio_session_manager_service() -> object:
    from src.services.audio_session_manager.service import AudioSessionManagerService

    return AudioSessionManagerService()


def build_audio_preprocessor() -> object:
    from src.services.audio_preprocessing.service import AudioPreprocessorService

    return AudioPreprocessorService()


def build_shared_call_dependencies() -> object:
    """Everything src/services/media_gateway/twilio_ws_entrypoint.py's
    Starlette app needs, constructed once for the life of the process —
    one MediaGatewayService/AudioSessionManagerService/AudioPreprocessor/
    STTService/ConversationEngine instance serves every call; only
    call-scoped state (AudioSession, VADEndpointingService, DialogueManager,
    PlaybackScheduler) is constructed fresh per connection inside
    CallOrchestrator itself.
    """
    from src.services.media_gateway.twilio_ws_entrypoint import SharedCallDependencies

    gpu_scheduler = build_gpu_scheduler()
    # Fresh CustomerService reading from the same authoritative Postgres —
    # /voice's ANI lookup and ConversationEngine's context assembly both
    # end up at the same customers table, so source-of-truth is shared
    # even though the client objects are constructed independently.
    customer_service = build_customer_service(build_postgres_connection())
    from src.services.tts.greeting_cache import GreetingCache
    greeting_cache = GreetingCache()
    return SharedCallDependencies(
        account_sid=_env("TWILIO_ACCOUNT_SID", required=True),
        auth_token=_env("TWILIO_AUTH_TOKEN", required=True),
        tenant_id=_env("DEFAULT_TENANT_ID", "tenant-default"),
        media_gateway_service=build_media_gateway_service(),
        audio_session_manager_service=build_audio_session_manager_service(),
        audio_preprocessor=build_audio_preprocessor(),
        stt_service=build_stt_service(gpu_scheduler),
        conversation_engine=build_conversation_engine(),
        customer_service=customer_service,
        language=_env("STT_LANGUAGE", "hi"),
        # See SharedCallDependencies.public_ws_base_url's docstring — required
        # whenever this process runs behind a tunnel/reverse-proxy (e.g. a
        # cloudflared quick tunnel), which is every real deployment: without
        # it, Twilio's X-Twilio-Signature check fails for every connection
        # because the internally-observed URL never matches what Twilio
        # actually signed. e.g. "wss://random-words.trycloudflare.com".
        public_ws_base_url=_env("PUBLIC_WS_BASE_URL", ""),
        recording_dir=_env("CALL_RECORDING_DIR", ""),
        greeting_timeout_s=float(_env("GREETING_TIMEOUT_S", "60.0")),
        greeting_cache=greeting_cache,
    )


# ---------------------------------------------------------------------------
# Dialer system
# ---------------------------------------------------------------------------


def build_dialer_services(conn: object, raw_redis: object) -> object:
    """Construct DialerSessionManager with all dependencies.

    Env vars consumed:
        TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN  — existing Twilio credentials
        TWILIO_CALLER_ID                        — E.164 number to call from
        TWIML_APP_URL                           — TwiML instruction URL
        DIALER_STATUS_CALLBACK_URL              — Twilio posts call events here
        DIALER_MAX_CONCURRENT                   — parallel in-flight calls (default 1)
    """
    from src.libs.repositories.campaign_lead import CampaignLeadRepository
    from src.services.dialer.engine import DialerEngine
    from src.services.dialer.outbound_call import TwilioOutboundCallService
    from src.services.dialer.queue import DialerQueue
    from src.services.dialer.session import DialerSessionManager

    twilio_caller_id = _env("TWILIO_CALLER_ID", required=True)
    twiml_app_url = _env("TWIML_APP_URL", required=True)
    status_callback_url = _env("DIALER_STATUS_CALLBACK_URL", required=True)
    max_concurrent = int(_env("DIALER_MAX_CONCURRENT", "1"))

    lead_repo = CampaignLeadRepository(conn)
    dialer_queue = DialerQueue(raw_redis)

    def _make_engine() -> DialerEngine:
        twilio = TwilioOutboundCallService(
            account_sid=_env("TWILIO_ACCOUNT_SID", required=True),
            auth_token=_env("TWILIO_AUTH_TOKEN", required=True),
            caller_id=twilio_caller_id,
            twiml_app_url=twiml_app_url,
            status_callback_url=status_callback_url,
        )
        return DialerEngine(
            twilio=twilio,
            dialer_queue=dialer_queue,
            lead_repo=lead_repo,
            redis=raw_redis,
            max_concurrent=max_concurrent,
        )

    return DialerSessionManager(
        engine_factory=_make_engine,
        dialer_queue=dialer_queue,
        lead_repo=lead_repo,
    )


def serve() -> None:
    """Bind the real Twilio Media Streams WebSocket entrypoint via uvicorn."""
    import uvicorn

    from src.services.media_gateway.twilio_ws_entrypoint import create_twilio_media_stream_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(_env("MEDIA_GATEWAY_PORT", "8010"))
    deps = build_shared_call_dependencies()
    app = create_twilio_media_stream_app(deps)  # type: ignore[arg-type]
    async def _warm_greeting_cache() -> None:
        try:
            from src.libs.contracts.streaming import VoiceConfig
            from src.engines.prompt_builder.kavya_persona import build_greeting_text
            lender = _env("LENDER_NAME", "Rajat Finance")
            customer_name = _env("GREETING_CACHE_CUSTOMER_NAME", "")
            greeting_text = build_greeting_text(customer_name=customer_name, lender_name=lender)
            tts_service = deps.conversation_engine._tts  # type: ignore[attr-defined]
            voice_config = VoiceConfig()
            logger.info("GreetingCache: scheduling warm-up (lender=%r customer_name=%r text_len=%d)", lender, customer_name, len(greeting_text))
            ok = await deps.greeting_cache.warm_up(greeting_text, tts_service, voice_config)  # type: ignore[union-attr]
            if ok:
                logger.info("GreetingCache: warm-up succeeded - greetings will splice from cache")
            else:
                logger.warning("GreetingCache: warm-up returned False - live TTS will handle greetings")
        except Exception:
            logger.exception("GreetingCache: warm-up crashed - live TTS will handle greetings")

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        import asyncio as _aio
        _aio.create_task(_warm_greeting_cache())
        yield

    app.router.lifespan_context = _lifespan
    logger.info("Serving Twilio Media Streams WS entrypoint on 0.0.0.0:%d/twilio/media-stream", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


# ---------------------------------------------------------------------------
# Smoke test — construct-only, no turn handled
# ---------------------------------------------------------------------------


def smoke_test() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Building full ConversationEngine dependency graph against real Postgres/Redis/GPU node...")
    engine = build_conversation_engine()
    assert engine is not None
    logger.info("OK — ConversationEngine constructed with every real dependency wired.")

    # Standalone construction check — build_conversation_engine() above
    # already wires an equivalent instance into ConversationEngine itself
    # (Phase 5); this just double-checks the builder function works in
    # isolation on its own fresh connection too.
    conn = build_postgres_connection()
    raw_redis = build_raw_redis_client()
    ptp_service = build_promise_to_pay_service(
        conn,
        build_idempotency_guard(conn),
        build_policy_engine_service(conn, raw_redis),
    )
    assert ptp_service is not None
    logger.info("OK — PromiseToPayService constructed and wired into ConversationEngine (Path-A Phase 5).")

    deps = build_shared_call_dependencies()
    assert deps is not None
    logger.info("OK — SharedCallDependencies constructed (Phase 4 Twilio WS entrypoint is ready to serve).")


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        smoke_test()
    elif "--serve" in sys.argv:
        serve()
    else:
        print(__doc__)
