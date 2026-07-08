"""Concurrency runtime primitives (V3 Ch9 — Concurrency Architecture).

WorkerPool (fair-share task execution), BoundedQueue (RI-3 enforcement),
LoadShedder (overload protection), and BackpressureSignal (producer
throttling) — the building blocks the per-call executor model composes.

Architecture: V3 Ch9, Ch10.
"""

from __future__ import annotations

from src.libs.concurrency.backpressure import BackpressureMonitor, BackpressureSignal
from src.libs.concurrency.bounded_queue import BoundedQueue, QueueFullError
from src.libs.concurrency.load_shedder import LoadShedder
from src.libs.concurrency.worker_pool import Priority, WorkerPool

__all__ = [
    "BackpressureMonitor",
    "BackpressureSignal",
    "BoundedQueue",
    "LoadShedder",
    "Priority",
    "QueueFullError",
    "WorkerPool",
]
