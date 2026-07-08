"""BoundedQueue — an ``asyncio.Queue`` with a mandatory, enforced ``max_size`` (RI-3).

Every buffer/queue in VoiceOS must declare a maximum depth (V1 Appendix E
RI-3; V3 Ch9 §9.12; V3 Ch10 §10.12). ``BoundedQueue`` is the one sanctioned
way to get a bounded, typed queue: overflow raises ``QueueFullError``
immediately rather than blocking indefinitely or silently dropping work,
so producers can react (backpressure, shed, or surface the error) instead
of hiding an unbounded-growth bug.

Architecture: V1 Appendix E RI-3; V3 Ch9 (Concurrency); V3 Ch10 (Queue Management).
"""

from __future__ import annotations

import asyncio


class QueueFullError(Exception):
    """Raised by :meth:`BoundedQueue.put_nowait` when the queue is at ``max_size``.

    This is the RI-3 enforcement signal: VoiceOS queues never block
    indefinitely and never silently drop work on overflow — the caller must
    explicitly decide what to do (retry, shed, backpressure the producer).
    """

    def __init__(self, queue_name: str, max_size: int) -> None:
        super().__init__(f"BoundedQueue '{queue_name}' is full (max_size={max_size})")
        self.queue_name = queue_name
        self.max_size = max_size


class BoundedQueue[T]:
    """A strictly bounded async queue — the one RI-3-compliant queue primitive.

    Wraps ``asyncio.Queue(maxsize=max_size)``. ``max_size`` is mandatory
    (no default, no "unbounded" option) so every instantiation site is a
    conscious capacity decision, per RI-3.
    """

    def __init__(self, max_size: int, *, name: str = "unnamed") -> None:
        """
        Args:
            max_size: Maximum number of items the queue may hold. Must be > 0.
            name: Human-readable queue name, used in error messages and metrics.

        Raises:
            ValueError: If ``max_size`` is not a positive integer.
        """
        if max_size <= 0:
            raise ValueError(f"BoundedQueue '{name}': max_size must be > 0, got {max_size}")
        self._max_size = max_size
        self._name = name
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=max_size)

    @property
    def max_size(self) -> int:
        """The declared maximum capacity (RI-3)."""
        return self._max_size

    @property
    def name(self) -> str:
        """The queue's human-readable name."""
        return self._name

    def qsize(self) -> int:
        """Current number of items in the queue."""
        return self._queue.qsize()

    def fill_ratio(self) -> float:
        """Current depth as a fraction of ``max_size`` (0.0-1.0+)."""
        return self._queue.qsize() / self._max_size

    def put_nowait(self, item: T) -> None:
        """Enqueue ``item`` without waiting.

        Raises:
            QueueFullError: If the queue is already at ``max_size`` (RI-3) —
                never blocks, never silently drops the item.
        """
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise QueueFullError(self._name, self._max_size) from exc

    async def put(self, item: T) -> None:
        """Enqueue ``item``, waiting for space if the queue is full.

        Use ``put_nowait()`` on the RI-3 enforcement path (where an
        immediate overflow signal is required); use this only where a
        producer legitimately wants backpressure-style waiting.
        """
        await self._queue.put(item)

    def get_nowait(self) -> T:
        """Dequeue an item without waiting.

        Raises:
            asyncio.QueueEmpty: If the queue is empty.
        """
        return self._queue.get_nowait()

    async def get(self) -> T:
        """Dequeue an item, waiting if the queue is empty."""
        return await self._queue.get()

    def empty(self) -> bool:
        """True if the queue currently holds no items."""
        return self._queue.empty()

    def full(self) -> bool:
        """True if the queue is at ``max_size``."""
        return self._queue.full()
