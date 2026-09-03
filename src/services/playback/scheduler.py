"""PlaybackScheduler — ordered audio clause queue with barge-in support.

The scheduler maintains a bounded queue of AudioClause objects ready for
transmission to the media layer. On barge-in it flushes all queued clauses
and signals upstream to stop TTS synthesis.

Architecture: V1 Ch18 (True Streaming Pipeline); V1 Ch21 (Playback).
Invariant: RI-3 (bounded queue — no unbounded growth on barge-in).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from src.libs.contracts.streaming import AudioClause
from src.libs.invariants.guards import assert_ri3_bounded_buffer
from src.libs.observability.metrics import record_queue_depth, record_queue_max_size

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DEPTH = 512  # ~43s of 85.33ms chunks; batch was 32 (3-10 clauses/turn)
_QUEUE_NAME = "playback_scheduler"


class PlaybackScheduler:
    """Bounded queue for AudioClause objects ready for playback.

    Thread-safe for use in asyncio contexts. All public methods are
    coroutines to simplify caller code.

    Args:
        max_depth: Maximum number of queued clauses before RI-3 violation.
    """

    def __init__(self, max_depth: int = _DEFAULT_MAX_DEPTH) -> None:
        self._max_depth = max_depth
        self._queue: deque[AudioClause] = deque()
        self._barge_in_event: asyncio.Event = asyncio.Event()
        self._clause_available: asyncio.Event = asyncio.Event()
        # Phase E — playback invalidation generation. Monotonically
        # increments on every flush() (i.e. every barge-in). Producers
        # snapshot this at the start of a synthesis scope and stamp each
        # AudioClause with it; enqueue() rejects anything whose generation
        # does not equal the current generation. This is the fail-closed
        # channel that guarantees a coroutine from gen N whose await
        # resumes AFTER a barge-in + clear_barge_in() cannot inject stale
        # audio into gen N+1's queue.
        self._generation: int = 0
        # Sprint-016 (V3 Ch10 §10.17): expose RI-3 max_depth + live depth to
        # Prometheus so hot-path queue saturation is visible on dashboards.
        record_queue_max_size(_QUEUE_NAME, max_depth)
        record_queue_depth(_QUEUE_NAME, 0)

    async def enqueue(self, clause: AudioClause) -> None:
        """Enqueue an AudioClause for playback.

        Args:
            clause: Synthesized AudioClause from the TTS pipeline.

        Raises:
            InvariantViolationError: If queue depth exceeds max_depth (RI-3).

        Phase E — fail-closed generation check: a clause whose
        ``generation`` does not equal ``self._generation`` is DROPPED with
        a warning and never enters the queue. This handles the delayed
        producer race where a coroutine synthesised under gen N resumes
        AFTER flush() has advanced the generation to N+1 (and after the
        barge-in event may have been cleared by the next turn starting).
        We do NOT rewrite the clause's generation to the current value —
        that would silently mask the race.
        """
        if clause.generation != self._generation:
            logger.warning(
                "PlaybackScheduler: dropping stale clause idx=%d "
                "(clause_gen=%d, current_gen=%d) — post-barge-in race",
                clause.clause_index,
                clause.generation,
                self._generation,
            )
            return
        assert_ri3_bounded_buffer(len(self._queue), self._max_depth, _QUEUE_NAME)
        self._queue.append(clause)
        record_queue_depth(_QUEUE_NAME, len(self._queue))
        self._clause_available.set()
        logger.debug(
            "PlaybackScheduler: enqueued clause %d (depth=%d, gen=%d)",
            clause.clause_index,
            len(self._queue),
            self._generation,
        )

    async def dequeue(self) -> AudioClause:
        """Wait for and return the next AudioClause.

        Returns:
            The oldest enqueued AudioClause (FIFO).
        """
        while not self._queue:
            self._clause_available.clear()
            await self._clause_available.wait()
        clause = self._queue.popleft()
        record_queue_depth(_QUEUE_NAME, len(self._queue))
        return clause

    def dequeue_nowait(self) -> AudioClause | None:
        """Pop the next AudioClause if one is queued, else return None immediately.

        Unlike dequeue(), never awaits — for callers draining "whatever this
        turn actually enqueued" (e.g. the WS entrypoint sending clauses it
        already received directly from ConversationEngine's return value)
        where the caller must not block if a test double/mocked engine
        returned clauses without ever calling enqueue() on this scheduler.
        """
        if not self._queue:
            return None
        clause = self._queue.popleft()
        record_queue_depth(_QUEUE_NAME, len(self._queue))
        return clause

    async def flush(self) -> list[AudioClause]:
        """Flush all queued clauses and signal barge-in.

        Called when the customer interrupts the agent response. Returns
        all previously queued clauses (for logging / replay purposes).

        Phase E — advances ``self._generation`` before anything else so
        every in-flight producer/consumer that snapshotted the previous
        generation is now definitively stale. This must happen BEFORE
        ``_barge_in_event.set()`` so a producer that races between the
        two ordering points (checks event → not yet set → then enqueues)
        still fails the generation check.

        Returns:
            List of clauses that were flushed.
        """
        self._generation += 1
        flushed = list(self._queue)
        self._queue.clear()
        record_queue_depth(_QUEUE_NAME, 0)
        self._clause_available.clear()
        self._barge_in_event.set()
        logger.info(
            "PlaybackScheduler: flush on barge-in — discarded %d clauses, "
            "generation now %d",
            len(flushed),
            self._generation,
        )
        return flushed

    def clear_barge_in(self) -> None:
        """Reset the barge-in signal for the next turn."""
        self._barge_in_event.clear()

    @property
    def barge_in_event(self) -> asyncio.Event:
        """The asyncio.Event set when a barge-in flush occurs."""
        return self._barge_in_event

    @property
    def generation(self) -> int:
        """Current playback invalidation generation (Phase E).

        Producers snapshot this at the start of their synthesis scope and
        stamp AudioClause.generation with it. Consumers compare against
        this value at every enqueue/release/send boundary and drop any
        clause whose generation is older. The counter increments in
        ``flush()`` only — it is monotonic for the lifetime of the
        scheduler instance and is NOT reset by ``clear_barge_in()``.
        """
        return self._generation

    def get_clauses(self) -> list[AudioClause]:
        """Return all queued clauses without removing them (for testing)."""
        return list(self._queue)

    @property
    def depth(self) -> int:
        """Current number of clauses in the queue."""
        return len(self._queue)
