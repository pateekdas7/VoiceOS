"""Priority queue for VRAM requests.

Requests are served in priority order (CRITICAL first). Within the same
priority level, FIFO order is preserved via a monotone sequence counter.

Priority levels (lower integer = higher priority):
    CRITICAL  0  — active in-call STT/TTS inference
    HIGH      1  — active in-call LLM inference
    NORMAL    2  — background LLM prefill
    LOW       3  — model preloading / warming

Architecture: V1 Ch7 (GPU Scheduler priority queue).
"""

from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field
from enum import IntEnum

# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------


class RequestPriority(IntEnum):
    """Priority levels for VRAM requests (lower = served first)."""

    CRITICAL = 0
    """Active in-call STT/TTS inference — must be served with minimum latency."""
    HIGH = 1
    """Active in-call LLM inference."""
    NORMAL = 2
    """Background LLM prefill."""
    LOW = 3
    """Model preloading and warming operations."""


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass
class VRAMRequest:
    """A pending VRAM request waiting to be served by the scheduler."""

    service: str
    """Requesting service name (e.g. 'stt', 'llm', 'tts')."""
    model: str
    """Model identifier (e.g. 'whisper-large-v3-turbo')."""
    vram_mb: int
    """VRAM required in megabytes."""
    priority: RequestPriority
    """Request priority — determines service order."""
    _seq: int = field(default=0, compare=False, repr=False)
    """Monotone sequence number for FIFO ordering within the same priority."""

    def __lt__(self, other: VRAMRequest) -> bool:
        if self.priority != other.priority:
            return int(self.priority) < int(other.priority)
        return self._seq < other._seq

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VRAMRequest):
            return NotImplemented
        return self.priority == other.priority and self._seq == other._seq

    def __le__(self, other: VRAMRequest) -> bool:
        return self == other or self < other

    def __gt__(self, other: VRAMRequest) -> bool:
        return not self <= other

    def __ge__(self, other: VRAMRequest) -> bool:
        return not self < other


# ---------------------------------------------------------------------------
# PriorityQueue
# ---------------------------------------------------------------------------


class PriorityQueue:
    """Thread-safe priority queue for VRAM requests.

    Uses a min-heap so lower-numbered priority values (CRITICAL=0) are served
    before higher-numbered ones.  FIFO ordering within a priority is enforced
    by the monotone ``_seq`` field on each VRAMRequest.

    Architecture: V1 Ch7 (priority queue); V7 Ch6.
    """

    def __init__(self) -> None:
        self._heap: list[VRAMRequest] = []
        self._lock: threading.Lock = threading.Lock()
        self._counter: int = 0

    def enqueue(self, request: VRAMRequest) -> None:
        """Add a request to the queue."""
        with self._lock:
            self._counter += 1
            request._seq = self._counter
            heapq.heappush(self._heap, request)

    def dequeue(self) -> VRAMRequest | None:
        """Remove and return the highest-priority request, or None if empty."""
        with self._lock:
            if not self._heap:
                return None
            return heapq.heappop(self._heap)

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)
