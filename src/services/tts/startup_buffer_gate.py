"""StartupBufferGate — audio startup buffering layer for the buffered_streaming TTS mode.

Sits between ``TrueStreamingPipeline`` and ``PlaybackScheduler``. Depending on
mode, it either passes clauses through untouched (``streaming``, the default),
holds them until a fixed ``threshold_ms`` of audio has accumulated then
releases FIFO into the scheduler (``buffered_streaming``), or holds every
clause until ``is_final=True`` is observed then releases (``blocking``).

Modes (env var ``VOICEOS_TTS_MODE``):
  - ``streaming`` (default)           — pass-through; no gate is instantiated.
  - ``buffered_streaming``            — accumulate until ``threshold_ms`` of
                                        audio is buffered, then release.
  - ``blocking``                      — accumulate every clause until the
                                        final one (``is_final=True``), then
                                        release.

Fixed startup-buffer values only (Phase B mandate): ``0 / 400 / 600 / 800 /
1000`` ms. No adaptive shrinking.

Design invariants (Phase D):
  - FIFO clause order strictly preserved on release.
  - ``is_final=True`` always triggers release regardless of buffered_ms, so
    end-of-turn audio is never trapped below threshold.
  - Barge-in immediately discards buffered clauses — ZERO stale audio can
    reach Twilio after cancellation.
  - Bounded buffer via ``max_buffered_clauses`` cap (RI-3 style) prevents
    unbounded growth on a runaway LLM.
  - ``streaming`` mode causes callers to skip the gate entirely: no new
    code path is exercised in the default configuration.

Architecture: V1 Ch18 (True Streaming Pipeline extension); Phase D.
"""

from __future__ import annotations

import enum
import logging
import os
from collections import deque
from collections.abc import Mapping

from src.libs.contracts.streaming import AudioClause
from src.services.playback.scheduler import PlaybackScheduler

logger = logging.getLogger(__name__)


_ENV_TTS_MODE = "VOICEOS_TTS_MODE"
_ENV_TTS_BUFFER_MS = "VOICEOS_TTS_BUFFER_MS"
_DEFAULT_THRESHOLD_MS = 400
_ALLOWED_THRESHOLD_MS: frozenset[int] = frozenset({0, 400, 600, 800, 1000})
# Phase E correction: the actual AudioClause payload reaching AudioOutput
# is PCM16LE (2 bytes/sample). The prior default of 4 came from the
# stale Veena docstring in veena_adapter.py which claimed float32 LE PCM;
# the WHOLE downstream code path (AudioOutput.convert with
# audioop.ratecv(..., 2, ...)) treats it as 16-bit. Duration accounting
# in this gate must match the wire format the rest of the system uses.
_DEFAULT_BYTES_PER_SAMPLE = 2
_DEFAULT_MAX_BUFFERED_CLAUSES = 128  # bounded buffer cap (runaway guard).


class TTSMode(str, enum.Enum):
    """Selects the TTS clause-to-playback dispatch strategy for one turn."""

    STREAMING = "streaming"
    BUFFERED_STREAMING = "buffered_streaming"
    BLOCKING = "blocking"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TTSMode:
        """Resolve the mode from ``VOICEOS_TTS_MODE``; default STREAMING.

        Unknown values log a warning and fall back to STREAMING so a bad
        env var never silently changes the audio path.
        """
        source = env if env is not None else os.environ
        raw = (source.get(_ENV_TTS_MODE) or "").strip().lower()
        if not raw:
            return cls.STREAMING
        try:
            return cls(raw)
        except ValueError:
            logger.warning(
                "Unknown %s=%r — defaulting to %s", _ENV_TTS_MODE, raw, cls.STREAMING.value
            )
            return cls.STREAMING


def threshold_ms_from_env(env: Mapping[str, str] | None = None) -> int:
    """Resolve ``VOICEOS_TTS_BUFFER_MS`` for buffered_streaming mode.

    Only the fixed sweep values 0/400/600/800/1000 are supported in Phase D
    (Phase B mandate — no adaptive shrinking, no arbitrary intermediate
    values in the first measurement pass). Non-integer or out-of-set values
    warn and fall back to the default 400 ms.
    """
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_TTS_BUFFER_MS) or "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD_MS
    try:
        v = int(raw)
    except ValueError:
        logger.warning(
            "Non-integer %s=%r — defaulting to %d",
            _ENV_TTS_BUFFER_MS, raw, _DEFAULT_THRESHOLD_MS,
        )
        return _DEFAULT_THRESHOLD_MS
    if v < 0:
        return 0
    if v not in _ALLOWED_THRESHOLD_MS:
        logger.warning(
            "%s=%d not in fixed sweep set %s — defaulting to %d",
            _ENV_TTS_BUFFER_MS, v, sorted(_ALLOWED_THRESHOLD_MS), _DEFAULT_THRESHOLD_MS,
        )
        return _DEFAULT_THRESHOLD_MS
    return v


class StartupBufferGate:
    """Per-turn bounded audio accumulator that opens after ``threshold_ms``.

    Not thread-safe — one instance per active turn. Typical lifecycle:

        gate = StartupBufferGate(playback, mode=TTSMode.BUFFERED_STREAMING,
                                 threshold_ms=600)
        # pipeline calls await gate.enqueue(clause) for each synth'd clause;
        # the first ~600ms of audio are buffered; then the buffer flushes
        # FIFO into the scheduler and subsequent clauses pass through.
        # At end of turn:
        await gate.flush_final()
        # On barge-in / abort:
        gate.discard()

    State machine::

        [ACCUMULATING] --accumulated_ms >= threshold_ms--> [RELEASED]
        [ACCUMULATING] --clause.is_final=True-----------> [RELEASED]
        [ACCUMULATING] --flush_final() called-----------> [RELEASED]
        [ACCUMULATING] --buffer cap hit-----------------> [RELEASED]
        [ACCUMULATING] --barge_in_event set-------------> [DISCARDED]
        [RELEASED]     --enqueue(clause)----------------> [RELEASED]  (pass-through)
        [DISCARDED]    --enqueue(clause)----------------> [DISCARDED] (dropped)

    Architecture: V1 Ch18 extension; Phase D.
    """

    def __init__(
        self,
        playback: PlaybackScheduler,
        mode: TTSMode,
        threshold_ms: int,
        *,
        max_buffered_clauses: int = _DEFAULT_MAX_BUFFERED_CLAUSES,
        bytes_per_sample: int = _DEFAULT_BYTES_PER_SAMPLE,
        generation: int | None = None,
    ) -> None:
        self._playback = playback
        self._mode = mode
        self._threshold_ms = max(0, int(threshold_ms))
        self._max_buffered_clauses = max(1, int(max_buffered_clauses))
        self._bytes_per_sample = max(1, int(bytes_per_sample))
        self._buffer: deque[AudioClause] = deque()
        self._buffered_ms: float = 0.0
        # Phase E — the playback generation this gate is scoped to. Snapshot
        # once at construction (or accept explicit override from the pipeline
        # that already snapshotted at its scope start). Any clause whose
        # AudioClause.generation does not equal this value is stale and must
        # NOT be forwarded to the scheduler — barge-in already advanced the
        # scheduler's generation, and the next turn may have cleared
        # barge_in_event, so a boolean-event check alone is insufficient.
        self._scope_generation: int = (
            int(generation) if generation is not None else playback.generation
        )
        # STREAMING should never be gated at all (callers skip construction)
        # but if instantiated anyway, treat it as released-from-start so
        # behavior stays a pure pass-through. Threshold=0 in buffered mode
        # is equivalent to streaming. BLOCKING waits for is_final regardless
        # of threshold — do not short-circuit it here.
        self._released: bool = (
            self._mode == TTSMode.STREAMING
            or (self._mode == TTSMode.BUFFERED_STREAMING and self._threshold_ms == 0)
        )
        self._discarded: bool = False

    # ---- read-only observability (used by tests + metrics) ----

    @property
    def mode(self) -> TTSMode:
        return self._mode

    @property
    def threshold_ms(self) -> int:
        return self._threshold_ms

    @property
    def released(self) -> bool:
        return self._released

    @property
    def buffered_ms(self) -> float:
        return self._buffered_ms

    @property
    def buffered_clauses(self) -> int:
        return len(self._buffer)

    @property
    def scope_generation(self) -> int:
        return self._scope_generation

    def _is_stale(self, clause: AudioClause) -> bool:
        """True if this clause belongs to a superseded generation.

        Compares BOTH the clause's own stamped generation and the current
        scheduler generation against the scope generation captured at
        construction. Any drift on either side means the barge-in boundary
        has been crossed and the clause must be dropped, not forwarded.
        """
        return (
            clause.generation != self._scope_generation
            or self._playback.generation != self._scope_generation
        )

    # ---- duration accounting ----

    def _clause_ms(self, clause: AudioClause) -> float:
        sr = clause.sample_rate if clause.sample_rate else 1
        return (len(clause.audio_data) / self._bytes_per_sample) / sr * 1000.0

    # ---- main API ----

    async def enqueue(self, clause: AudioClause) -> None:
        """Enqueue one AudioClause, buffered or pass-through per mode.

        - If barge-in fires (either already or between enqueues), the buffer
          is dropped and further enqueues become no-ops until ``discard()``
          / a new gate is constructed for the next turn.
        - If already released, forwards straight to the scheduler.
        - If accumulating, appends to the buffer; may trigger release when
          the threshold is crossed, when ``is_final=True``, or when the
          bounded buffer cap is hit.
        """
        if self._discarded:
            return

        # Phase E — fail-closed generation check runs BEFORE the boolean
        # barge_in_event check. A coroutine synthesised under gen N whose
        # await resumed AFTER flush() advanced the scheduler AND the next
        # turn cleared barge_in_event would otherwise pass the event test
        # and inject stale audio. The generation check catches it.
        if self._is_stale(clause):
            logger.warning(
                "StartupBufferGate: dropping stale clause idx=%d "
                "(clause_gen=%d, scope_gen=%d, playback_gen=%d) — "
                "discarding %d buffered",
                clause.clause_index, clause.generation,
                self._scope_generation, self._playback.generation,
                len(self._buffer),
            )
            self.discard()
            return

        if self._playback.barge_in_event.is_set():
            logger.debug(
                "StartupBufferGate: barge-in observed at enqueue — discarding "
                "buffered=%d, dropping clause idx=%d",
                len(self._buffer), clause.clause_index,
            )
            self.discard()
            return

        if self._released:
            await self._playback.enqueue(clause)
            return

        # Bounded buffer: force release if the cap is reached rather than
        # letting the deque grow without limit. This preserves ordering:
        # existing buffered clauses flush first, then the new clause.
        if len(self._buffer) >= self._max_buffered_clauses:
            logger.warning(
                "StartupBufferGate: buffer cap %d hit (buffered_ms=%.1f, "
                "mode=%s) — forcing release",
                self._max_buffered_clauses, self._buffered_ms, self._mode.value,
            )
            await self._release_buffered()
            if not self._discarded:
                await self._playback.enqueue(clause)
            return

        self._buffer.append(clause)
        self._buffered_ms += self._clause_ms(clause)

        should_release = False
        if self._mode == TTSMode.BUFFERED_STREAMING and self._buffered_ms >= self._threshold_ms:
            should_release = True
        if clause.is_final:
            # End-of-turn: always release regardless of mode/threshold.
            # This is the sole release trigger in BLOCKING mode.
            should_release = True

        if should_release:
            await self._release_buffered()

    async def flush_final(self) -> None:
        """Force release of any remaining buffered clauses.

        Called by the pipeline once the token stream finishes. No-op if
        already released or already discarded. If barge-in has fired, drops
        the buffer without forwarding.
        """
        if self._discarded:
            return
        if self._playback.barge_in_event.is_set():
            self.discard()
            return
        await self._release_buffered()

    async def _release_buffered(self) -> None:
        if not self._buffer:
            self._released = True
            return
        # Snapshot len for logging; drain by popleft to preserve FIFO order
        # and to allow barge-in to interrupt mid-drain.
        n = len(self._buffer)
        while self._buffer:
            # Phase E — check the generation FIRST (fail-closed, catches
            # the race where the scheduler advanced but barge_in_event
            # was already cleared by the next turn). Then still check
            # barge_in_event as a belt-and-braces guard.
            if self._playback.generation != self._scope_generation:
                logger.warning(
                    "StartupBufferGate: playback generation advanced during "
                    "release (scope_gen=%d, playback_gen=%d) — dropping %d "
                    "remaining buffered clauses",
                    self._scope_generation, self._playback.generation,
                    len(self._buffer),
                )
                self.discard()
                return
            if self._playback.barge_in_event.is_set():
                logger.info(
                    "StartupBufferGate: barge-in during release — dropping "
                    "%d remaining buffered clauses",
                    len(self._buffer),
                )
                self.discard()
                return
            clause = self._buffer.popleft()
            if clause.generation != self._scope_generation:
                logger.warning(
                    "StartupBufferGate: buffered clause idx=%d stale "
                    "(clause_gen=%d, scope_gen=%d) — dropping",
                    clause.clause_index, clause.generation,
                    self._scope_generation,
                )
                continue
            await self._playback.enqueue(clause)
        self._buffered_ms = 0.0
        self._released = True
        logger.debug("StartupBufferGate: released %d buffered clauses", n)

    def discard(self) -> None:
        """Drop any buffered clauses without enqueueing (barge-in / abort).

        After ``discard()`` all subsequent ``enqueue()`` calls are no-ops.
        Callers construct a fresh gate for the next turn.
        """
        n = len(self._buffer)
        if n:
            logger.info(
                "StartupBufferGate: discarding %d buffered clauses "
                "(barge-in/abort)",
                n,
            )
        self._buffer.clear()
        self._buffered_ms = 0.0
        self._discarded = True
        self._released = True


def build_gate_from_env(
    playback: PlaybackScheduler,
    env: Mapping[str, str] | None = None,
    *,
    generation: int | None = None,
) -> StartupBufferGate | None:
    """Construct a gate from env, or return None for the default streaming mode.

    Returned ``None`` means callers should skip the gate entirely and
    ``await playback.enqueue(...)`` directly — the same execution path as
    before Phase D (emitted clause boundaries may differ under the Phase C
    single-authoritative splitter).

    ``generation``: Phase E — the scope generation snapshotted by the
    caller (typically ``playback.generation`` captured at the start of
    ``TrueStreamingPipeline.run``). If ``None`` the gate snapshots
    itself, which is safe when the caller has not already crossed an
    await boundary since the intended scope start.
    """
    mode = TTSMode.from_env(env)
    if mode == TTSMode.STREAMING:
        return None
    return StartupBufferGate(
        playback=playback,
        mode=mode,
        threshold_ms=threshold_ms_from_env(env),
        generation=generation,
    )
