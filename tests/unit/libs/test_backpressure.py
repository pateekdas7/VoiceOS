"""Unit tests for BackpressureMonitor/BackpressureSignal (V3 Ch9 §9.11)."""

from __future__ import annotations

import pytest

from src.libs.concurrency.backpressure import BackpressureMonitor
from src.libs.concurrency.bounded_queue import BoundedQueue


class TestBackpressureMonitorConstruction:
    def test_rejects_out_of_range_high_water_ratio(self) -> None:
        with pytest.raises(ValueError):
            BackpressureMonitor(high_water_ratio=1.5)


class TestBackpressureSignalForQueue:
    def test_inactive_below_high_water_mark(self) -> None:
        monitor = BackpressureMonitor(high_water_ratio=0.8)
        queue: BoundedQueue[int] = BoundedQueue(max_size=10, name="delivery-queue")
        for i in range(5):
            queue.put_nowait(i)

        signal = monitor.signal(queue)

        assert signal.queue_name == "delivery-queue"
        assert signal.fill_ratio == 0.5
        assert signal.active is False

    def test_active_above_high_water_mark(self) -> None:
        monitor = BackpressureMonitor(high_water_ratio=0.8)
        queue: BoundedQueue[int] = BoundedQueue(max_size=10, name="delivery-queue")
        for i in range(9):
            queue.put_nowait(i)

        signal = monitor.signal(queue)

        assert signal.fill_ratio == 0.9
        assert signal.active is True


class TestBackpressureSignalForRawDepth:
    def test_signal_for_computes_ratio_without_a_queue_object(self) -> None:
        monitor = BackpressureMonitor(high_water_ratio=0.8)

        signal = monitor.signal_for("playback-buffer", current_size=850, max_size=1000)

        assert signal.fill_ratio == 0.85
        assert signal.active is True
