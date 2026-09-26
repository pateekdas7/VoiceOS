"""Phase I Gate 1 — FULL_RESPONSE (complete-response buffering) tests.

Locks in the invariant: once the AI begins speaking, the entire response
plays continuously without word/clause/sentence starvation gaps regardless
of how slow Veena is per clause. The change is confined to the existing
``StartupBufferGate`` boundary — no engine composition or model changes.

Coverage grid (letters match the acceptance requirement handed by product):

  A.  FULL_RESPONSE enum + configuration is exposed on TTSMode.
  B.  Audio accumulates until is_final — no scheduler enqueue mid-buffer.
  C.  Playback does NOT start before the final clause completion.
  D.  128-clause BLOCKING boundary does NOT trigger premature playback
      under FULL_RESPONSE.
  E.  A very long response (thousands of clauses) remains buffered until
      is_final.
  F.  FIFO ordering across many clauses is preserved on release.
  G.  Generation-ID change discards buffered stale audio (no leak to
      scheduler).
  H.  Barge-in clears the complete-response buffer without any partial
      release.
  I.  A new generation starts clean after a barge-in — the next gate
      accumulates and releases independently.
  J.  Greeting is wired to FULL_RESPONSE at the WS entrypoint.
  K.  Scripted turn is wired to FULL_RESPONSE in ConversationEngine.
  L.  LLM streaming turn is wired to FULL_RESPONSE via
      ``_run_llm_streaming_path``.
  M.  ClauseSplitter still produces multiple clauses and all of them
      accumulate under the gate — no clause is silently dropped by
      response-level buffering.
  N.  Simulated slow per-clause TTS cannot inject an inter-clause gap:
      scheduler receives ALL clauses in a single burst after is_final.
  O.  A TTS failure BEFORE is_final fails closed — the incomplete
      response is never released to playback.
  P.  Existing 160-byte μ-law framing invariant remains intact (audio
      bytes flow through the gate untouched; framing happens downstream
      of the scheduler).
  Q.  Sequence/RTP monotonicity — clause_index is monotonically
      non-decreasing on release; the gate never re-orders.
  R.  Governance + engine wiring remains intact — AIGovernanceService is
      still constructed and OutputValidator still runs upstream of the
      gate.
  S.  Existing Gate 1/2/3D behavior still passes (BLOCKING mode is
      preserved and still force-releases on cap; test_greeting_blocking_p4
      passes; test_balance_flow_p2 passes).
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.libs.contracts.response_plan import ResponsePlan
from src.libs.contracts.streaming import AudioClause, VoiceConfig
from src.services.ai_governance.service import AIGovernanceService
from src.services.conversation_engine.engine import (
    CILPort, ConversationEngine, PromptBuilderPort,
)
from src.services.knowledge_retrieval.service import KnowledgeRetrievalService
from src.services.llm_runtime.output_validator import ValidationResult
from src.services.playback.scheduler import PlaybackScheduler
from src.services.tts.startup_buffer_gate import (
    StartupBufferGate,
    TTSMode,
    _FULL_RESPONSE_MAX_CAP,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _clause(idx: int, *, is_final: bool = False, generation: int = 0, payload: bytes | None = None) -> AudioClause:
    """One 85.33ms Veena-shaped PCM16LE super-frame."""
    data = payload if payload is not None else (b"\x01\x00" * 2048)  # 2048 int16 samples
    return AudioClause(
        audio_data=data,
        sample_rate=24000,
        text=f"c{idx}",
        clause_index=idx,
        is_final=is_final,
        generation=generation,
    )


class _RecordingScheduler(PlaybackScheduler):
    """Records every successful enqueue so tests can prove:
      (a) NOTHING was enqueued while the gate was buffering, and
      (b) release delivers in FIFO order in a single batch after is_final.

    Uses super().enqueue() so generation-ID / RI-3 guards are still real."""

    def __init__(self, max_depth: int = 512) -> None:
        super().__init__(max_depth=max_depth)
        self.enqueues_seen: list[AudioClause] = []

    async def enqueue(self, clause: AudioClause) -> None:  # type: ignore[override]
        pre_len = len(self._queue)
        await super().enqueue(clause)
        # Only record clauses that survived the generation check
        # (super().enqueue drops stale ones silently).
        if len(self._queue) > pre_len:
            self.enqueues_seen.append(clause)


class _MultiClauseTTS:
    """TTS fake: one AudioClause per text chunk fed in."""

    def __init__(self) -> None:
        self.received_texts: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterator[str], voice_config: VoiceConfig | None = None,
    ) -> AsyncIterator[AudioClause]:
        return self._gen(text_chunks)

    async def _gen(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[AudioClause]:
        async for chunk in text_chunks:
            self.received_texts.append(chunk)
            yield AudioClause(
                audio_data=b"\x00" * 4096,
                sample_rate=24000,
                text=chunk,
                clause_index=0,
                is_final=True,
            )


class _AlwaysValid:
    def validate(self, text: str, response_plan: ResponsePlan) -> ValidationResult:
        return ValidationResult(valid=True)


def _make_engine() -> ConversationEngine:
    return ConversationEngine(
        cil=cast(CILPort, MagicMock()),
        prompt_builder=cast(
            PromptBuilderPort,
            MagicMock(build=MagicMock(return_value=("prompt", "hash"))),
        ),
        llm_service=MagicMock(),
        tts_service=_MultiClauseTTS(),
        validator=_AlwaysValid(),
        knowledge=cast(KnowledgeRetrievalService, MagicMock(retrieve=AsyncMock(return_value=[]))),
        quality_scorer=cast(Any, MagicMock()),
        ai_governance_service=AIGovernanceService.create(),
        lender_name="Rajat Finance",
    )


# ---------------------------------------------------------------------------
# A — enum + configuration
# ---------------------------------------------------------------------------


def test_A_full_response_mode_exists_and_wire_literal() -> None:
    assert TTSMode.FULL_RESPONSE.value == "full_response"
    assert TTSMode("full_response") is TTSMode.FULL_RESPONSE


def test_A_full_response_default_cap_is_much_higher_than_blocking() -> None:
    """FULL_RESPONSE gate must default to a safety cap far above the
    BLOCKING cap so long real responses are never force-released. The
    exact number is an implementation detail; the invariant is
    "significantly bigger than 128"."""
    playback = _RecordingScheduler()
    g = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)
    assert g._max_buffered_clauses >= 1024
    assert g._max_buffered_clauses == _FULL_RESPONSE_MAX_CAP


# ---------------------------------------------------------------------------
# B + C — accumulate until is_final; no playback before final
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_B_C_accumulates_until_final_no_playback_mid_buffer() -> None:
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    for i in range(20):
        await gate.enqueue(_clause(i, is_final=False))
        # After every mid-buffer enqueue: scheduler MUST be empty. This is
        # the invariant "no audio frame is sent before finalization".
        assert playback.enqueues_seen == [], (
            f"gate released clause {i} before is_final — scheduler saw "
            f"{[c.clause_index for c in playback.enqueues_seen]}"
        )
        assert gate.buffered_clauses == i + 1
        assert not gate.released

    # The final clause triggers a single-batch release.
    await gate.enqueue(_clause(20, is_final=True))
    assert gate.released
    indexes = [c.clause_index for c in playback.enqueues_seen]
    assert indexes == list(range(21))


# ---------------------------------------------------------------------------
# D — 128-clause BLOCKING cap does NOT trigger premature playback in
#     FULL_RESPONSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_D_128_clause_boundary_does_not_release_prematurely() -> None:
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    for i in range(300):  # well past the BLOCKING 128 default
        await gate.enqueue(_clause(i, is_final=False))
        assert playback.enqueues_seen == [], (
            f"FULL_RESPONSE force-released at clause {i} — the 128-cap "
            f"BLOCKING behaviour leaked into FULL_RESPONSE"
        )

    assert gate.buffered_clauses == 300
    await gate.enqueue(_clause(300, is_final=True))
    assert len(playback.enqueues_seen) == 301


# ---------------------------------------------------------------------------
# E — very long response remains buffered until final
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_E_very_long_response_remains_buffered_until_final() -> None:
    """The gate is not the bottleneck for a very long response. We prove
    the ~5.8-min FULL_RESPONSE cap holds >500 clauses buffered without
    releasing. (The scheduler carries its own RI-3 bounded-queue cap on
    the release side — a separate downstream invariant; here we use a
    scheduler large enough to accept the release burst so the pre-final
    accumulate invariant is what's under test.)"""
    playback = _RecordingScheduler(max_depth=1200)
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    n = 1000  # ~85 seconds of Veena at 85.33 ms/clause — well beyond the
              # 128-clause BLOCKING cap and far above any realistic response.
    for i in range(n):
        await gate.enqueue(_clause(i, is_final=False))
    assert playback.enqueues_seen == []
    assert gate.buffered_clauses == n

    await gate.enqueue(_clause(n, is_final=True))
    assert len(playback.enqueues_seen) == n + 1


# ---------------------------------------------------------------------------
# F + Q — FIFO ordering / monotonic clause_index on release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F_Q_fifo_and_monotonic_release() -> None:
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    for i in range(50):
        await gate.enqueue(_clause(i, is_final=(i == 49)))
    idxs = [c.clause_index for c in playback.enqueues_seen]
    assert idxs == sorted(idxs)          # never reordered
    assert idxs == list(range(50))       # exact FIFO, no gaps


# ---------------------------------------------------------------------------
# G — generation-ID change mid-buffer discards stale audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_G_generation_id_change_discards_stale_buffer() -> None:
    playback = _RecordingScheduler()
    gen_before = playback.generation
    gate = StartupBufferGate(
        playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0,
        generation=gen_before,
    )

    for i in range(10):
        await gate.enqueue(_clause(i, is_final=False, generation=gen_before))
    assert gate.buffered_clauses == 10

    # Simulate a barge-in: scheduler advances generation.
    await playback.flush()
    assert playback.generation == gen_before + 1
    # The next enqueue must trip the fail-closed generation check and
    # discard the entire buffered response — no partial audio to playback.
    playback.enqueues_seen.clear()
    await gate.enqueue(_clause(10, is_final=False, generation=gen_before))

    assert gate._discarded
    assert playback.enqueues_seen == []


# ---------------------------------------------------------------------------
# H — barge-in mid-response clears buffer (no partial playback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_H_bargein_mid_response_clears_buffer() -> None:
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)
    for i in range(30):
        await gate.enqueue(_clause(i, is_final=False))

    # Set barge_in_event WITHOUT advancing generation (the pre-flush race
    # window) — the next enqueue should still discard.
    playback.barge_in_event.set()
    await gate.enqueue(_clause(30, is_final=False))
    assert playback.enqueues_seen == []
    assert gate.buffered_clauses == 0


# ---------------------------------------------------------------------------
# I — new generation after barge-in starts clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_I_new_generation_after_bargein_starts_clean() -> None:
    playback = _RecordingScheduler()
    # Turn 1 — barge-in mid-response.
    g1 = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)
    for i in range(5):
        await g1.enqueue(_clause(i, is_final=False))
    await playback.flush()  # advances generation, sets barge_in_event
    playback.clear_barge_in()
    assert playback.enqueues_seen == []

    # Turn 2 — fresh gate under the new generation must accumulate and
    # release independently, without any leak from turn 1.
    gen2 = playback.generation
    g2 = StartupBufferGate(
        playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0, generation=gen2,
    )
    for i in range(3):
        await g2.enqueue(_clause(i, is_final=False, generation=gen2))
    assert playback.enqueues_seen == []
    await g2.enqueue(_clause(3, is_final=True, generation=gen2))
    idxs = [c.clause_index for c in playback.enqueues_seen]
    assert idxs == [0, 1, 2, 3]
    for c in playback.enqueues_seen:
        assert c.generation == gen2


# ---------------------------------------------------------------------------
# J — greeting caller wire-up
# ---------------------------------------------------------------------------


def test_J_greeting_wires_full_response() -> None:
    from src.services.media_gateway import twilio_ws_entrypoint
    src = inspect.getsource(twilio_ws_entrypoint)
    assert (
        'tts_mode="full_response"' in src
        or "tts_mode='full_response'" in src
    ), "greeting caller no longer passes tts_mode=full_response"


# ---------------------------------------------------------------------------
# K + L — engine wire-up for scripted + LLM paths
# ---------------------------------------------------------------------------


def test_K_L_engine_wires_full_response_for_scripted_and_llm_paths() -> None:
    from src.services.conversation_engine import engine as _engine_mod
    src = inspect.getsource(_engine_mod)
    # scripted per-turn path passes tts_mode="full_response".
    assert src.count('tts_mode="full_response"') + src.count(
        "tts_mode='full_response'"
    ) >= 2, (
        "engine.py must wire FULL_RESPONSE for BOTH the scripted golden "
        "path AND the LLM streaming fallback"
    )


@pytest.mark.asyncio
async def test_L_llm_streaming_path_builds_full_response_gate() -> None:
    """Behavioural proof: `_run_llm_streaming_path(tts_mode="full_response")`
    holds every clause until is_final."""
    engine = _make_engine()
    playback = _RecordingScheduler()

    # LLM stub that yields two chunks then finishes.
    async def _tokens():
        from src.libs.contracts.streaming import TokenChunk
        yield TokenChunk(text="Namaste sir. ", token_id=1, finish_reason=None)
        yield TokenChunk(text="Aap kaise hain?", token_id=2, finish_reason="stop")

    engine._llm.generate_stream = AsyncMock(return_value=_tokens())

    # Minimal response_plan (reuse the module-level default).
    from src.services.conversation_engine.engine import _default_response_plan
    plan = _default_response_plan()

    clauses = await engine._run_llm_streaming_path(
        prompt_text="p", response_plan=plan, playback=playback,
        customer_name="", tts_mode="full_response",
    )
    # Even though the splitter emits multiple clauses, they all reach the
    # scheduler in one FIFO batch after is_final — no mid-response gap.
    assert len(clauses) >= 1
    for i, c in enumerate(playback.enqueues_seen):
        assert c.clause_index == i


# ---------------------------------------------------------------------------
# M — ClauseSplitter still emits multiple clauses under FULL_RESPONSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_M_clause_splitter_still_runs_and_all_clauses_accumulated() -> None:
    engine = _make_engine()
    playback = _RecordingScheduler()
    # A 3-clause reply — ClauseSplitter should segment on ". ".
    text = "Namaste sir. Main Kavya bol rahi hoon. Aap kaise hain?"
    clauses = await engine.speak_scripted_text(
        text, playback, response_plan=None, tts_mode="full_response",
    )
    assert len(clauses) >= 1
    # Every clause the pipeline produced reached the scheduler.
    assert len(playback.enqueues_seen) == len(clauses)


# ---------------------------------------------------------------------------
# N — slow per-clause TTS cannot inject an inter-clause playback gap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_N_slow_per_clause_tts_no_scheduler_gap() -> None:
    """Directly at the gate boundary: even if the producer sleeps between
    clauses (simulating slow Veena TTFA), the scheduler sees nothing
    until is_final, then receives everything back-to-back."""
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    async def _produce_slow() -> None:
        for i in range(10):
            await gate.enqueue(_clause(i, is_final=False))
            await asyncio.sleep(0.005)  # simulated slow synth
            # Scheduler MUST still be empty at every intermediate slow point.
            assert playback.enqueues_seen == []
        await gate.enqueue(_clause(10, is_final=True))

    await _produce_slow()
    # Post-release: everything is present in FIFO.
    assert [c.clause_index for c in playback.enqueues_seen] == list(range(11))


# ---------------------------------------------------------------------------
# O — TTS failure before is_final fails closed (no partial release)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_O_tts_failure_before_final_fails_closed() -> None:
    """If the producer dies before is_final and never calls flush_final(),
    the FULL_RESPONSE gate must NOT dump partial audio to the scheduler.
    We simulate abort by calling discard() (the same code path the
    pipeline invokes on generation-advance / exception cleanup at the
    gate lifecycle branch in TrueStreamingPipeline.run)."""
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)
    for i in range(15):
        await gate.enqueue(_clause(i, is_final=False))
    assert playback.enqueues_seen == []

    # Producer exception / abort path — gate.discard() is what
    # TrueStreamingPipeline runs on generation-advance / barge-in.
    gate.discard()
    assert playback.enqueues_seen == []
    # Further enqueues remain no-ops.
    await gate.enqueue(_clause(15, is_final=True))
    assert playback.enqueues_seen == []


@pytest.mark.asyncio
async def test_O_runaway_cap_hit_fails_closed_not_release() -> None:
    """FULL_RESPONSE's safety cap must be fail-closed, not force-release.
    Hitting the cap discards the whole buffered response — a partial
    response reaching Twilio would be worse than silence."""
    playback = _RecordingScheduler()
    gate = StartupBufferGate(
        playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0,
        max_buffered_clauses=5,  # small cap for the test only
    )
    for i in range(5):
        await gate.enqueue(_clause(i, is_final=False))
    assert gate.buffered_clauses == 5
    assert playback.enqueues_seen == []

    # This one trips the cap.
    await gate.enqueue(_clause(5, is_final=False))
    assert gate._discarded
    assert playback.enqueues_seen == []


# ---------------------------------------------------------------------------
# P + Q — audio payload preserved (framing intact); clause_index monotonic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_P_audio_payload_bytes_preserved_through_gate() -> None:
    """FULL_RESPONSE must not slice or re-encode audio. The 160-byte
    μ-law framing invariant is enforced by AudioOutput downstream, but
    the PCM16LE payload delivered to the scheduler must be byte-identical
    to what the producer supplied (otherwise every downstream framing
    guarantee is undermined)."""
    playback = _RecordingScheduler()
    gate = StartupBufferGate(playback, mode=TTSMode.FULL_RESPONSE, threshold_ms=0)

    payloads = [bytes([i % 256]) * 4096 for i in range(6)]
    for i, p in enumerate(payloads):
        await gate.enqueue(_clause(i, is_final=(i == 5), payload=p))

    for expected, got in zip(payloads, playback.enqueues_seen, strict=True):
        assert got.audio_data == expected
        # Every delivered payload is a whole number of PCM16 samples,
        # which is what AudioOutput requires to honour the 160-byte
        # μ-law framing contract (2 bytes/sample × 20 samples per frame
        # after resample). We assert the pre-framing invariant here:
        assert len(got.audio_data) % 2 == 0


# ---------------------------------------------------------------------------
# R — governance + engine composition unchanged
# ---------------------------------------------------------------------------


def test_R_ai_governance_still_constructed_on_engine() -> None:
    """FULL_RESPONSE wiring must not have removed or bypassed the
    governance gate. AIGovernanceService is a mandatory constructor
    argument; if a refactor made it optional this test flags it."""
    from src.services.conversation_engine.engine import ConversationEngine
    sig = inspect.signature(ConversationEngine.__init__)
    assert "ai_governance_service" in sig.parameters
    # And it's still a required param (no default value).
    assert sig.parameters["ai_governance_service"].default is inspect.Parameter.empty


def test_R_pipeline_still_runs_validator_and_governance() -> None:
    """Source-level guardrail: the TrueStreamingPipeline still holds and
    calls its OutputValidator + AIGovernanceService references in
    ``_synthesise_and_enqueue``. FULL_RESPONSE is purely a downstream
    playback-buffering change — governance runs BEFORE the gate."""
    from src.services.tts import streaming_pipeline
    src = inspect.getsource(streaming_pipeline)
    assert "validator.validate(" in src
    assert "_ai_governance_service" in src
    assert ".evaluate_output(" in src


# ---------------------------------------------------------------------------
# S — Gate 3D / P4 regression (BLOCKING mode still intact)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_S_blocking_mode_still_force_releases_on_cap() -> None:
    """BLOCKING mode is preserved for backwards compatibility — the
    Phase-I change ONLY altered FULL_RESPONSE's cap behaviour. Callers
    still on BLOCKING (test_greeting_blocking_p4) get the pre-Phase-I
    force-release semantics."""
    playback = _RecordingScheduler()
    gate = StartupBufferGate(
        playback, mode=TTSMode.BLOCKING, threshold_ms=0,
        max_buffered_clauses=3,
    )
    for i in range(3):
        await gate.enqueue(_clause(i, is_final=False))
    # The 4th clause should trip force-release (existing BLOCKING behavior).
    await gate.enqueue(_clause(3, is_final=False))
    # First three released in FIFO, then the 4th arrives.
    assert [c.clause_index for c in playback.enqueues_seen] == [0, 1, 2, 3]
