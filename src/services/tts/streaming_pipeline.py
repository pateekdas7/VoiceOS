"""TrueStreamingPipeline — clause-level LLM→TTS streaming with barge-in support.

Accumulates LLM token stream into speakable clauses (split at '. ? ! ;'),
synthesises each clause as it becomes available, and pushes AudioClauses
to the PlaybackScheduler. Synthesis starts after the first complete clause
is ready — not after the full response. This minimises first-audio latency.

On barge-in (PlaybackScheduler.barge_in_event fires), synthesis is
abandoned and the method returns immediately.

Architecture: V1 Ch18 (True Streaming Pipeline).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator

from src.libs.ai_safety.register_guard import RegisterGuard, dedupe_name, sanitize_reply, strip_trailing_sir
from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import AudioClause, TokenChunk, VoiceConfig
from src.services.ai_governance.service import AIGovernanceService
from src.services.ai_governance.verdict import SAFE_FALLBACK_RESPONSE, GovernanceStatus
from src.services.llm_runtime.output_validator import OutputValidator, ValidationResult
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.clause_splitter import ClauseSplitter
from src.services.tts.service import TTSService
from src.services.tts.startup_buffer_gate import StartupBufferGate, build_gate_from_env

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


class TrueStreamingPipeline:
    """Orchestrates the LLM token stream through validation, AI Governance, and TTS to playback.

    Architecture: V1 Ch18; V4 Ch3 (AI Governance gate, Sprint-018).

    Usage:
        pipeline = TrueStreamingPipeline(ai_governance_service=ai_governance_service)
        clauses = await pipeline.run(token_stream, response_plan, tts, validator, playback)
    """

    def __init__(self, ai_governance_service: AIGovernanceService | None = None) -> None:
        # Optional here so this class stays independently unit-testable;
        # ConversationEngine (Sprint-018) always constructs this pipeline
        # with a real AIGovernanceService — the gate is mandatory at that
        # call site, not at this one (see ConversationEngine.__init__).
        self._ai_governance_service = ai_governance_service
        self._register_guard = RegisterGuard()

    async def run(
        self,
        token_stream: AsyncIterator[TokenChunk],
        response_plan: ResponsePlan,
        tts_service: TTSService,
        validator: OutputValidator,
        playback: PlaybackScheduler,
        customer_name: str = "",
        gate: StartupBufferGate | None = None,
    ) -> list[AudioClause]:
        """Drive tokens through validation, TTS, and playback.

        Args:
            token_stream: AsyncIterator of TokenChunks from the LLM.
            response_plan: The sealed ResponsePlan for this turn.
            tts_service: TTSService instance for synthesis.
            validator: OutputValidator to check each candidate clause.
            playback: PlaybackScheduler to receive synthesised clauses.
            customer_name: When non-empty, every clause is scrubbed of this
                name before synthesis (Kavya persona rule 6 — never address
                the customer by name). Empty string is a deliberate no-op,
                not just "no name available": callers that intentionally
                speak the customer's name (the AWAIT_IDENTITY greeting/
                reask — see DialogueResponseEngine._handle_await_identity)
                must omit this argument rather than pass it and rely on
                scrubbing removing it again. Found missing entirely for the
                real LLM/TTS streaming path during Call-002 readiness
                validation — the scripted golden path was already clean via
                DialogueResponseEngine's own RegisterGuard pass, but a real
                LLM-generated reply addressed the customer by name and used
                a blocked literary word with nothing here to catch it.

        Returns:
            List of all synthesised AudioClauses (ordered, non-barged-in only).
        """
        splitter = ClauseSplitter()
        clause_index = 0
        all_clauses: list[AudioClause] = []
        full_output_parts: list[str] = []
        finish_seen = False

        # Phase E — snapshot the playback generation ONCE at scope start.
        # Every AudioClause synthesised in this run() belongs to this
        # generation; if flush() advances the scheduler mid-turn every
        # remaining producer/consumer that still holds this snapshot must
        # abort. We do not read playback.generation again on the hot path
        # — the enqueue/gate/scheduler will re-compare, but our own bail
        # decisions are driven by comparing the snapshot to the live
        # value.
        scope_generation = playback.generation

        # Phase D (Sprint-031): opt-in audio startup buffering. When the
        # caller does not provide a gate we consult VOICEOS_TTS_MODE — if
        # the mode is `streaming` (the default) `build_gate_from_env`
        # returns None and no gating code runs at all. Only `buffered_streaming`
        # or `blocking` introduce a gate. Phase E: propagate the scope
        # generation so the gate rejects/discards on the same boundary.
        if gate is None:
            gate = build_gate_from_env(playback, generation=scope_generation)

        async for chunk in token_stream:
            # Phase E — the fail-closed generation comparison catches the
            # subtle case where barge_in_event has already been cleared by
            # the next turn: barge_in_event.is_set() would then return
            # False, but playback.generation still shows the advance.
            if (
                playback.generation != scope_generation
                or playback.barge_in_event.is_set()
            ):
                logger.info(
                    "TrueStreamingPipeline: barge-in/gen-advance detected "
                    "(scope_gen=%d, playback_gen=%d) — aborting",
                    scope_generation, playback.generation,
                )
                break

            full_output_parts.append(chunk.text)

            # Feed tokens to the single authoritative ClauseSplitter. Any
            # complete clauses (. ? ! ; ।, digit-decimals and abbreviations
            # guarded) are emitted immediately. Short valid replies such as
            # "हाँ।" (no trailing whitespace) are held here and emerge via
            # flush() below — never dropped.
            for clause_text in splitter.feed(chunk.text):
                if (
                    playback.generation != scope_generation
                    or playback.barge_in_event.is_set()
                ):
                    logger.info(
                        "TrueStreamingPipeline: barge-in/gen-advance "
                        "detected mid-clause-loop — aborting"
                    )
                    break
                clauses = await self._synthesise_and_enqueue(
                    clause_text,
                    clause_index,
                    response_plan,
                    tts_service,
                    validator,
                    playback,
                    is_final=False,
                    customer_name=customer_name,
                    gate=gate,
                    generation=scope_generation,
                )
                all_clauses.extend(clauses)
                clause_index += len(clauses)

            if chunk.finish_reason is not None:
                finish_seen = True
                break

        if (
            playback.generation == scope_generation
            and not playback.barge_in_event.is_set()
        ):
            final_text = splitter.flush()
            if final_text:
                clauses = await self._synthesise_and_enqueue(
                    final_text,
                    clause_index,
                    response_plan,
                    tts_service,
                    validator,
                    playback,
                    is_final=True,
                    customer_name=customer_name,
                    gate=gate,
                    generation=scope_generation,
                )
                all_clauses.extend(clauses)

        # Gate lifecycle: barge-in / generation-advance → drop everything
        # still buffered so it can never reach Twilio; clean end-of-turn
        # → release remaining (only relevant for BLOCKING mode when the
        # final clause never arrived, or for BUFFERED_STREAMING when the
        # response was shorter than the threshold). Phase E — the
        # generation compare handles the post-barge-in-clear race that a
        # bare barge_in_event.is_set() check would miss.
        if gate is not None:
            if (
                playback.generation != scope_generation
                or playback.barge_in_event.is_set()
            ):
                gate.discard()
            else:
                await gate.flush_final()

        # finish_seen is retained for future observability hooks; not currently
        # required by callers.
        _ = finish_seen
        return all_clauses

    async def _synthesise_and_enqueue(
        self,
        text: str,
        clause_index: int,
        response_plan: ResponsePlan,
        tts: TTSService,
        validator: OutputValidator,
        playback: PlaybackScheduler,
        is_final: bool,
        customer_name: str = "",
        gate: StartupBufferGate | None = None,
        generation: int = 0,
    ) -> list[AudioClause]:
        """Validate text then synthesise it and push to playback queue."""
        result: ValidationResult = validator.validate(text, response_plan)
        if not result.valid:
            if is_final and result.fallback_response:
                # Use fallback for the final clause on rejection.
                text = result.fallback_response
            else:
                logger.warning(
                    "TrueStreamingPipeline: clause rejected — skipping synthesis",
                    extra={"violations": result.violations},
                )
                return []

        # AI Governance gate (V4 Ch3, Sprint-018) — every piece of LLM
        # output is evaluated here, before it reaches TTS. BLOCK/REQUIRE_HUMAN
        # both suppress synthesis for this clause; BLOCK substitutes a safe
        # fallback for the final clause (never invents replacement content).
        if self._ai_governance_service is not None:
            verdict = self._ai_governance_service.evaluate_output(
                text, response_plan, call_id=response_plan.call_id, tenant_id=response_plan.tenant_id
            )
            if verdict.status != GovernanceStatus.APPROVE:
                logger.warning(
                    "TrueStreamingPipeline: AI Governance %s — skipping synthesis",
                    verdict.status.value,
                    extra={"violations": verdict.violations, "explanation": verdict.explanation},
                )
                if is_final and verdict.status == GovernanceStatus.BLOCK:
                    text = SAFE_FALLBACK_RESPONSE
                else:
                    return []

        # Persona/register gate (Kavya rules, V2 Ch13) — the LAST gate
        # before speech, same as DialogueResponseEngine's own guard pass
        # on the scripted path (src/libs/ai_safety/register_guard.py).
        # Runs here so it applies uniformly to BOTH paths through this
        # pipeline: a scripted reply arrives already clean (this is then a
        # harmless no-op), but an LLM-generated reply had nothing else
        # enforcing register/name rules on it at all before this existed.
        if customer_name:
            text = dedupe_name(text, customer_name)
        text = sanitize_reply(text)
        text = strip_trailing_sir(text)
        register_result = self._register_guard.check(text)
        if not register_result.clean:
            logger.warning(
                "TrueStreamingPipeline: register/persona violation (%s) — skipping synthesis",
                register_result.violation,
            )
            if is_final:
                text = SAFE_FALLBACK_RESPONSE
            else:
                return []

        voice_config = VoiceConfig(
            rate_scale=response_plan.delivery.target_speaking_rate,
            language=response_plan.delivery.language,
        )

        synthesised: list[AudioClause] = []
        clause_stream = await tts.synthesize_stream(
            text_chunks=_single_chunk_stream(text),
            voice_config=voice_config,
        )
        async for audio_clause in clause_stream:
            # Phase E — the generation check runs BEFORE the event check
            # and covers the barge-in-then-clear race. Breaking out here
            # closes the httpx stream via the underlying generator's
            # finally block (see VeenaAdapter._stream_clause's
            # ``await resp.aclose()``), which is the practical mechanism
            # by which VeenaAdapter stops producing further stale audio.
            if (
                playback.generation != generation
                or playback.barge_in_event.is_set()
            ):
                break
            # Rebuild with correct clause_index, is_final flag, and stamp
            # with the scope generation so every downstream comparator
            # (StartupBufferGate.enqueue, PlaybackScheduler.enqueue,
            # _send_clause) can drop it fail-closed if the generation
            # has since advanced.
            reindexed = AudioClause(
                audio_data=audio_clause.audio_data,
                sample_rate=audio_clause.sample_rate,
                text=audio_clause.text,
                clause_index=clause_index + len(synthesised),
                is_final=is_final and audio_clause.is_final,
                generation=generation,
            )
            if gate is not None:
                await gate.enqueue(reindexed)
            else:
                await playback.enqueue(reindexed)
            synthesised.append(reindexed)

        return synthesised


async def _single_chunk_stream(text: str) -> AsyncGenerator[str, None]:
    """Yield a single text string as an async iterator."""
    yield text
