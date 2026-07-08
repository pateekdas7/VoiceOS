"""Unit tests for BoundedQueue (RI-3 enforcement, V3 Ch9/Ch10)."""

from __future__ import annotations

import asyncio

import pytest

from src.libs.concurrency.bounded_queue import BoundedQueue, QueueFullError


class TestBoundedQueueConstruction:
    def test_rejects_non_positive_max_size(self) -> None:
        with pytest.raises(ValueError):
            BoundedQueue(max_size=0)

        with pytest.raises(ValueError):
            BoundedQueue(max_size=-1)

    def test_exposes_max_size_and_name(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=10, name="turn-queue")

        assert queue.max_size == 10
        assert queue.name == "turn-queue"


class TestBoundedQueueOverflow:
    def test_bounded_queue_overflow_raises(self) -> None:
        """Required Sprint-016 test: put max_size+1 items -> QueueFullError (RI-3)."""
        queue: BoundedQueue[int] = BoundedQueue(max_size=3, name="test-queue")

        queue.put_nowait(1)
        queue.put_nowait(2)
        queue.put_nowait(3)

        with pytest.raises(QueueFullError) as exc_info:
            queue.put_nowait(4)

        assert exc_info.value.queue_name == "test-queue"
        assert exc_info.value.max_size == 3

    def test_never_silently_drops_or_blocks(self) -> None:
        """RI-3: overflow always raises — never a silent drop, never an indefinite block."""
        queue: BoundedQueue[str] = BoundedQueue(max_size=1)
        queue.put_nowait("a")

        with pytest.raises(QueueFullError):
            queue.put_nowait("b")

        # The original item is still there — nothing was silently dropped.
        assert queue.qsize() == 1
        assert queue.get_nowait() == "a"


class TestBoundedQueueOperations:
    def test_fill_ratio(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=4)
        assert queue.fill_ratio() == 0.0

        queue.put_nowait(1)
        queue.put_nowait(2)
        assert queue.fill_ratio() == 0.5

    async def test_get_waits_for_item(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=2)

        async def producer() -> None:
            await asyncio.sleep(0.01)
            queue.put_nowait(42)

        producer_task = asyncio.create_task(producer())
        result = await queue.get()
        await producer_task

        assert result == 42

    async def test_put_waits_when_full_then_succeeds_after_get(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=1)
        queue.put_nowait(1)

        async def consumer() -> None:
            await asyncio.sleep(0.01)
            queue.get_nowait()

        consumer_task = asyncio.create_task(consumer())
        await queue.put(2)  # waits until the consumer frees a slot
        await consumer_task

        assert queue.get_nowait() == 2

    def test_empty_and_full(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=1)
        assert queue.empty() is True
        assert queue.full() is False

        queue.put_nowait(1)
        assert queue.empty() is False
        assert queue.full() is True

    def test_get_nowait_raises_when_empty(self) -> None:
        queue: BoundedQueue[int] = BoundedQueue(max_size=1)
        with pytest.raises(asyncio.QueueEmpty):
            queue.get_nowait()
