"""WorkerPool — bounded-concurrency async task pool with fair-share scheduling.

Cognition/delivery work runs on a shared pool of ``max_workers`` async
workers pulling from a priority queue: higher-priority work is dispatched
first, and tasks of equal priority are served FIFO (fair-share within a
lane), matching V3 Ch9 §9.12's "weighted fair-share across calls/tenants"
model in its simplest single-tenant form.

Architecture: V3 Ch9 (Concurrency Architecture) §9.7, §9.12.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    """Task priority — higher value is served first (V3 Ch9 §9.12 priority lanes)."""

    SPECULATIVE = 0
    LOW = 1
    NORMAL = 2
    HIGH = 3


@dataclass(order=True)
class _QueuedTask[T]:
    """An internal priority-queue entry. Only ``sort_key`` participates in ordering."""

    sort_key: tuple[int, int]
    priority: Priority = field(compare=False)
    task: Callable[[], Awaitable[T]] = field(compare=False)
    future: asyncio.Future[T] = field(compare=False)


class WorkerPool:
    """A bounded pool of ``max_workers`` async workers with priority dispatch.

    Submitted tasks queue in priority order (ties broken FIFO) and are run
    by whichever of the ``max_workers`` background workers becomes free —
    concurrency is capped at ``max_workers`` regardless of how many tasks
    are submitted.
    """

    def __init__(self, max_workers: int) -> None:
        """
        Args:
            max_workers: Maximum number of tasks that may run concurrently. Must be > 0.

        Raises:
            ValueError: If ``max_workers`` is not a positive integer.
        """
        if max_workers <= 0:
            raise ValueError(f"WorkerPool: max_workers must be > 0, got {max_workers}")
        self._max_workers = max_workers
        self._pending: asyncio.PriorityQueue[_QueuedTask[Any]] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._workers: list[asyncio.Task[None]] = []
        self._active_count = 0

    @property
    def max_workers(self) -> int:
        """Configured maximum concurrent workers."""
        return self._max_workers

    @property
    def active_count(self) -> int:
        """Number of tasks currently executing (not counting queued-but-not-started work)."""
        return self._active_count

    @property
    def pending_count(self) -> int:
        """Number of tasks queued and waiting for a free worker."""
        return self._pending.qsize()

    async def submit[T](self, task: Callable[[], Awaitable[T]], priority: Priority = Priority.NORMAL) -> T:
        """Submit a task for fair-share execution and await its result.

        Args:
            task: A zero-argument async callable to execute.
            priority: Scheduling priority — higher values are dispatched first
                among currently-queued tasks (ties broken FIFO).

        Returns:
            Whatever ``task()`` returns.

        Raises:
            Exception: Whatever exception ``task()`` raises, propagated to the caller.
        """
        self._ensure_workers_started()
        sort_key = (-int(priority), next(self._seq))
        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        await self._pending.put(_QueuedTask(sort_key=sort_key, priority=priority, task=task, future=future))
        return await future

    def _ensure_workers_started(self) -> None:
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker_loop()) for _ in range(self._max_workers)]

    async def _worker_loop(self) -> None:
        while True:
            queued = await self._pending.get()
            self._active_count += 1
            try:
                result = await queued.task()
            except Exception as exc:  # propagate to the submitting caller's future, not to the worker loop
                if not queued.future.done():
                    queued.future.set_exception(exc)
            else:
                if not queued.future.done():
                    queued.future.set_result(result)
            finally:
                self._active_count -= 1
                self._pending.task_done()

    async def shutdown(self) -> None:
        """Cancel all background worker tasks. Call when the pool is no longer needed."""
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._workers = []
