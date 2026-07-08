"""Unit tests for LoadShedder (V3 Ch9 §9.12, §9.16)."""

from __future__ import annotations

import pytest

from src.libs.concurrency.load_shedder import LoadShedder
from src.libs.concurrency.worker_pool import Priority


class TestLoadShedderConstruction:
    def test_rejects_out_of_range_threshold(self) -> None:
        with pytest.raises(ValueError):
            LoadShedder(threshold=1.5)
        with pytest.raises(ValueError):
            LoadShedder(threshold=-0.1)


class TestLoadShedderBelowThreshold:
    def test_admits_everything_below_threshold(self) -> None:
        shedder = LoadShedder(threshold=0.8)

        for priority in Priority:
            assert shedder.should_shed(current_load=0.5, priority=priority) is False


class TestLoadShedderAboveThreshold:
    def test_sheds_speculative_first(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.should_shed(current_load=0.9, priority=Priority.SPECULATIVE) is True

    def test_sheds_low_priority(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.should_shed(current_load=0.9, priority=Priority.LOW) is True

    def test_never_sheds_high_priority(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.should_shed(current_load=5.0, priority=Priority.HIGH) is False

    def test_normal_priority_survives_moderate_overload(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.should_shed(current_load=0.85, priority=Priority.NORMAL) is False

    def test_normal_priority_shed_at_full_overload(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.should_shed(current_load=1.2, priority=Priority.NORMAL) is True


class TestLoadShedderAdmit:
    def test_admit_is_inverse_of_should_shed(self) -> None:
        shedder = LoadShedder(threshold=0.8)
        assert shedder.admit(current_load=0.5, priority=Priority.LOW) is True
        assert shedder.admit(current_load=0.95, priority=Priority.LOW) is False
