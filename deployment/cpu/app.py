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


def build_customer_context_assembler(conn: object) -> object:
    from src.libs.repositories.consent import ConsentRepository
    from src.libs.repositories.emi_schedule import EMIScheduleRepository
    from src.libs.repositories.loan_account import LoanAccountRepository
    from src.services.collections.emi_schedule import EMIScheduleService
    from src.services.collections.loan_account import LoanAccountService
    from src.services.crm.context_assembler import CustomerContextAssembler
    from src.services.crm.party import PartyService
    from src.services.crm.service import CustomerService

    crm_repos = build_crm_repositories(conn)
    customer_service = CustomerService(repositories=crm_repos)
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


def build_llm_service(gpu_scheduler: object) -> object:
    from src.services.llm_runtime.adapters.vllm_adapter import vLLMAdapter
    from src.services.llm_runtime.prompt_contract import PromptContract
    from src.services.llm_runtime.service import LLMService, LLMServiceConfig

    gpu_host = _env("GPU_NODE_HOST", required=True)
    base_url = f"http://{gpu_host}:8000"
    adapter = vLLMAdapter(gpu_scheduler=gpu_scheduler, prompt_contract=PromptContract(), base_url=base_url)
    return LLMService.create(adapter=adapter, config=LLMServiceConfig(base_url=base_url))


def build_tts_service(gpu_scheduler: object) -> object:
    from src.services.tts.adapters.veena_adapter import VeenaAdapter
    from src.services.tts.service import TTSService, TTSServiceConfig

    gpu_host = _env("GPU_NODE_HOST", required=True)
    base_url = f"http://{gpu_host}:8200"
    adapter = VeenaAdapter(gpu_scheduler=gpu_scheduler, base_url=base_url)
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
    from src.engines.strategy.engine import StrategyEngine

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
    )


# ---------------------------------------------------------------------------
# Path-A Phase 6f/6g — the scripted-response golden path
# ---------------------------------------------------------------------------


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
    )
    return engine


# ---------------------------------------------------------------------------
# Phase 4 — Twilio Media Streams WS entrypoint dependencies
# ---------------------------------------------------------------------------


def build_stt_service(gpu_scheduler: object) -> object:
    """Real GPU-backed STT via WhisperHTTPAdapter (Path-A Phase 3) — the
    piece that let this composition root reach the GPU node's /transcribe
    endpoint for the first time."""
    from src.services.stt.adapters.whisper_http_adapter import WhisperHTTPAdapter
    from src.services.stt.service import STTService, STTServiceConfig

    gpu_host = _env("GPU_NODE_HOST", required=True)
    adapter = WhisperHTTPAdapter(gpu_scheduler=gpu_scheduler, base_url=f"http://{gpu_host}:8100")
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
    return SharedCallDependencies(
        account_sid=_env("TWILIO_ACCOUNT_SID", required=True),
        auth_token=_env("TWILIO_AUTH_TOKEN", required=True),
        tenant_id=_env("DEFAULT_TENANT_ID", "tenant-default"),
        media_gateway_service=build_media_gateway_service(),
        audio_session_manager_service=build_audio_session_manager_service(),
        audio_preprocessor=build_audio_preprocessor(),
        stt_service=build_stt_service(gpu_scheduler),
        conversation_engine=build_conversation_engine(),
        language=_env("STT_LANGUAGE", "hi"),
        # See SharedCallDependencies.public_ws_base_url's docstring — required
        # whenever this process runs behind a tunnel/reverse-proxy (e.g. a
        # cloudflared quick tunnel), which is every real deployment: without
        # it, Twilio's X-Twilio-Signature check fails for every connection
        # because the internally-observed URL never matches what Twilio
        # actually signed. e.g. "wss://random-words.trycloudflare.com".
        public_ws_base_url=_env("PUBLIC_WS_BASE_URL", ""),
        recording_dir=_env("CALL_RECORDING_DIR", ""),
        greeting_timeout_s=float(_env("GREETING_TIMEOUT_S", "15.0")),
    )


def serve() -> None:
    """Bind the real Twilio Media Streams WebSocket entrypoint via uvicorn."""
    import uvicorn

    from src.services.media_gateway.twilio_ws_entrypoint import create_twilio_media_stream_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(_env("MEDIA_GATEWAY_PORT", "8010"))
    deps = build_shared_call_dependencies()
    app = create_twilio_media_stream_app(deps)  # type: ignore[arg-type]
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
