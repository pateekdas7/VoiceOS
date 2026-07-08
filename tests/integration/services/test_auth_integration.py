"""Integration tests for Sprint-018 (Authentication, Authorization/RBAC & AI Governance).

Exercises real cross-component behavior — no live Postgres/Redis/GPU
required for these (Sprint-018.md Phase 1 table: "in-process PolicyEngine
stub", mock JWT). Live infrastructure validation happens in Phase 2.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.libs.contracts.response_plan import FactMap, ResponsePlan, StrategyAction, StrategyLabel
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.ai_governance.service import AIGovernanceService
from src.services.auth.jwt_validator import JWTValidator, issue_test_token
from src.services.auth.service import AuthService
from src.services.authz.models import AuthorizationOutcome, AuthorizationRequest
from src.services.authz.roles import Role
from src.services.authz.service import AuthzService
from src.services.authz.tenant_isolation import TenantIsolationViolationError
from src.services.llm_runtime.output_validator import OutputValidator
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.service import TTSService, TTSServiceConfig
from src.services.tts.streaming_pipeline import TrueStreamingPipeline


class _MockTTSAdapter:
    """Echoes each text chunk back as a single AudioClause (no real inference)."""

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig
    ) -> AsyncIterator[AudioClause]:
        async def _gen() -> AsyncGenerator[AudioClause, None]:
            collected: list[str] = []
            async for chunk in text_chunks:
                collected.append(chunk)
            text = "".join(collected)
            yield AudioClause(audio_data=b"\x00" * 128, sample_rate=24_000, text=text, clause_index=0, is_final=True)

        return _gen()


async def _single_token_stream(text: str) -> AsyncIterator[TokenChunk]:
    async def _gen() -> AsyncGenerator[TokenChunk, None]:
        yield TokenChunk(text=text, token_id=1, finish_reason="stop")

    return _gen()


def _response_plan(facts: FactMap | None = None) -> ResponsePlan:
    return ResponsePlan(
        plan_id=str(uuid.uuid4()),
        version=1,
        call_id="call-1",
        tenant_id="tenant-1",
        created_at=datetime.utcnow(),
        strategy=StrategyAction(action=StrategyLabel.ASK),
        facts=facts or {},
    )


class TestAIGovernanceGateBlocksHallucinationEndToEnd:
    """The mandatory gate (Sprint-018 DoD) sits inside TrueStreamingPipeline,
    exactly where ConversationEngine wires it — before TTS synthesis."""

    async def test_hallucinated_amount_never_reaches_tts(self) -> None:
        pipeline = TrueStreamingPipeline(ai_governance_service=AIGovernanceService.create())
        tts_service = TTSService.create(adapter=_MockTTSAdapter(), config=TTSServiceConfig())
        playback = PlaybackScheduler()
        plan = _response_plan(facts={"outstanding_balance_minor": 1_250_000})  # ₹12,500 authoritative

        # ₹13,000 is within OutputValidator's 25%-deviation tolerance (so
        # *that* gate does not fire) but is not an exact match — only the
        # AI Governance gate's exact-match Law of Authority check catches it.
        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Aapka bakaya ₹13,000 hai."),
            response_plan=plan,
            tts_service=tts_service,
            validator=OutputValidator(),
            playback=playback,
        )

        # The mock TTS adapter echoes text back after TTSService's Hindi
        # script conversion stage, so the fallback text is compared after
        # the same transform (Roman-script Hindi words become Devanagari)
        # rather than against the raw SAFE_FALLBACK_RESPONSE constant.
        assert len(clauses) == 1
        assert "13,000" not in clauses[0].text
        assert clauses[0].text != "Aapka bakaya ₹13,000 hai."

    async def test_grounded_amount_reaches_tts_unmodified(self) -> None:
        pipeline = TrueStreamingPipeline(ai_governance_service=AIGovernanceService.create())
        tts_service = TTSService.create(adapter=_MockTTSAdapter(), config=TTSServiceConfig())
        playback = PlaybackScheduler()
        plan = _response_plan(facts={"outstanding_balance_minor": 1_250_000})  # ₹12,500

        clauses = await pipeline.run(
            token_stream=await _single_token_stream("Aapka bakaya ₹12,500 hai."),
            response_plan=plan,
            tts_service=tts_service,
            validator=OutputValidator(),
            playback=playback,
        )

        assert len(clauses) == 1
        assert "12,500" in clauses[0].text


class TestAuthThenAuthzChain:
    """JWTValidator -> AuthContext -> AuthzService, the shape every real PEP uses."""

    def test_valid_token_same_tenant_permits(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        auth_service = AuthService(jwt_validator=JWTValidator(public_key=private_key.public_key()))
        token = issue_test_token(private_key, subject="admin-1", tenant_id="tenant-1", role=Role.ADMIN.value)

        auth_context = auth_service.authenticate(authorization_header=f"Bearer {token}")
        result = AuthzService().authorize(
            AuthorizationRequest(auth_context=auth_context, action="POST", resource_tenant_id="tenant-1")
        )

        assert result.outcome == AuthorizationOutcome.PERMIT

    def test_valid_token_cross_tenant_raises(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        auth_service = AuthService(jwt_validator=JWTValidator(public_key=private_key.public_key()))
        token = issue_test_token(private_key, subject="admin-1", tenant_id="tenant-A", role=Role.ADMIN.value)

        auth_context = auth_service.authenticate(authorization_header=f"Bearer {token}")

        with pytest.raises(TenantIsolationViolationError):
            AuthzService().authorize(
                AuthorizationRequest(auth_context=auth_context, action="POST", resource_tenant_id="tenant-B")
            )
