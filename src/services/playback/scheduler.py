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
        """
        assert_ri3_bounded_buffer(len(self._queue), self._max_depth, _QUEUE_NAME)
        self._queue.append(clause)
        record_queue_depth(_QUEUE_NAME, len(self._queue))
        self._clause_available.set()
        logger.debug(
            "PlaybackScheduler: enqueued clause %d (depth=%d)",
            clause.clause_index,
            len(self._queue),
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

        Returns:
            List of clauses that were flushed.
        """
        flushed = list(self._queue)
        self._queue.clear()
        record_queue_depth(_QUEUE_NAME, 0)
        self._clause_available.clear()
        self._barge_in_event.set()
        logger.info("PlaybackScheduler: flush on barge-in — discarded %d clauses", len(flushed))
        return flushed

    def clear_barge_in(self) -> None:
        """Reset the barge-in signal for the next turn."""
        self._barge_in_event.clear()

    @property
    def barge_in_event(self) -> asyncio.Event:
        """The asyncio.Event set when a barge-in flush occurs."""
        return self._barge_in_event

    def get_clauses(self) -> list[AudioClause]:
        """Return all queued clauses without removing them (for testing)."""
        return list(self._queue)

    @property
    def depth(self) -> int:
        """Current number of clauses in the queue."""
        return len(self._queue)
