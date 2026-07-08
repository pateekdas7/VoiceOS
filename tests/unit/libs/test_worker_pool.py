"""Unit tests for WorkerPool (bounded-concurrency, priority dispatch — V3 Ch9 §9.12)."""

from __future__ import annotations

import asyncio

import pytest

from src.libs.concurrency.worker_pool import Priority, WorkerPool


class TestWorkerPoolConstruction:
    def test_rejects_non_positive_max_workers(self) -> None:
        with pytest.raises(ValueError):
            WorkerPool(max_workers=0)


class TestWorkerPoolExecution:
    async def test_submit_returns_task_result(self) -> None:
        pool = WorkerPool(max_workers=2)

        async def task() -> int:
            return 7

        result = await pool.submit(task)

        assert result == 7
        await pool.shutdown()

    async def test_submit_propagates_exceptions(self) -> None:
        pool = WorkerPool(max_workers=1)

        async def failing_task() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await pool.submit(failing_task)
        await pool.shutdown()

    async def test_bounds_concurrency_to_max_workers(self) -> None:
        pool = WorkerPool(max_workers=2)
        concurrent_count = 0
        max_observed = 0
        lock = asyncio.Lock()

        async def tracked_task() -> None:
            nonlocal concurrent_count, max_observed
            async with lock:
                concurrent_count += 1
                max_observed = max(max_observed, concurrent_count)
            await asyncio.sleep(0.02)
            async with lock:
                concurrent_count -= 1

        await asyncio.gather(*(pool.submit(tracked_task) for _ in range(6)))

        assert max_observed <= 2
        await pool.shutdown()

    async def test_higher_priority_dispatched_before_lower(self) -> None:
        pool = WorkerPool(max_workers=1)
        started = asyncio.Event()
        release = asyncio.Event()
        execution_order: list[str] = []

        async def blocker() -> None:
            started.set()
            await release.wait()

        async def low_task() -> None:
            execution_order.append("low")

        async def high_task() -> None:
            execution_order.append("high")

        blocker_future = asyncio.ensure_future(pool.submit(blocker, Priority.NORMAL))
        await started.wait()

        low_future = asyncio.ensure_future(pool.submit(low_task, Priority.LOW))
        high_future = asyncio.ensure_future(pool.submit(high_task, Priority.HIGH))
        await asyncio.sleep(0.01)  # let both queue up behind the blocker

        release.set()
        await asyncio.gather(blocker_future, low_future, high_future)

        assert execution_order == ["high", "low"]
        await pool.shutdown()

    async def test_active_and_pending_counts(self) -> None:
        pool = WorkerPool(max_workers=1)
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocker() -> None:
            started.set()
            await release.wait()

        blocker_future = asyncio.ensure_future(pool.submit(blocker))
        await started.wait()

        async def noop() -> None:
            pass

        queued_future = asyncio.ensure_future(pool.submit(noop))
        await asyncio.sleep(0.01)

        assert pool.active_count == 1
        assert pool.pending_count == 1

        release.set()
        await asyncio.gather(blocker_future, queued_future)
        await pool.shutdown()
