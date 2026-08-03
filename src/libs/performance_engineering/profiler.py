"""ContinuousProfiler — per-stage latency profiling harness, always-on in staging (V3 Ch19).

Architecture: V3 Ch19 (Performance Engineering).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Generic, Protocol, TypeVar

T = TypeVar("T")


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile of a pre-sorted list (p in 0-100 scale)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (p / 100.0) * (len(sorted_values) - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_values):
        return sorted_values[-1]
    frac = idx - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


@dataclass
class TimingResult(Generic[T]):  # noqa: UP046
    """The outcome of one profiled stage execution — timing metadata and the stage's return value."""

    stage: str
    duration_ms: float
    timestamp: datetime
    value: T


class BaselineRepository(Protocol):
    """Persistence contract for per-stage performance baselines."""

    def record(
        self,
        stage: str,
        p50_ms: float,
        p95_ms: float,
        p99_ms: float,
        measured_date: date,
    ) -> None:
        """Persist one day's p50/p95/p99 for ``stage``."""
        ...

    def fetch_latest_p95(self, stage: str) -> float | None:
        """Return the most-recently stored p95 for ``stage``, or ``None`` if no record exists."""
        ...


@dataclass
class InMemoryBaselineRepository:
    """In-memory baseline store — used in unit tests and Phase 1 validation."""

    _records: dict[tuple[str, date], dict[str, float]] = field(default_factory=dict)

    def record(
        self,
        stage: str,
        p50_ms: float,
        p95_ms: float,
        p99_ms: float,
        measured_date: date,
    ) -> None:
        self._records[(stage, measured_date)] = {
            "p50": p50_ms,
            "p95": p95_ms,
            "p99": p99_ms,
        }

    def fetch_latest_p95(self, stage: str) -> float | None:
        matching = [(k, v) for k, v in self._records.items() if k[0] == stage]
        if not matching:
            return None
        _, record = max(matching, key=lambda kv: kv[0][1])
        return record["p95"]


class ContinuousProfiler:
    """Always-on per-stage latency profiling harness (V3 Ch19).

    Call ``profile_stage(name, fn)`` to time a zero-argument callable; the
    result carries both the function's return value and the measured duration.
    Call ``flush()`` at the end of each day's observation window to persist
    accumulated p50/p95/p99 to the baseline store.
    """

    def __init__(self, store: BaselineRepository) -> None:
        self._store = store
        self._stage_samples: dict[str, list[float]] = {}

    def profile_stage(self, stage_name: str, fn: Callable[[], T]) -> TimingResult[T]:
        """Call ``fn``, measure its wall-clock duration, and return a ``TimingResult``.

        The timing is also accumulated internally; call ``flush()`` to persist.
        """
        start = time.perf_counter()
        value = fn()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self._stage_samples.setdefault(stage_name, []).append(elapsed_ms)

        return TimingResult(
            stage=stage_name,
            duration_ms=elapsed_ms,
            timestamp=datetime.now(tz=UTC),
            value=value,
        )

    def flush(self) -> None:
        """Persist accumulated p50/p95/p99 per stage to the baseline store and reset."""
        today = date.today()
        for stage, samples in self._stage_samples.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            self._store.record(
                stage=stage,
                p50_ms=_percentile(sorted_s, 50),
                p95_ms=_percentile(sorted_s, 95),
                p99_ms=_percentile(sorted_s, 99),
                measured_date=today,
            )
        self._stage_samples.clear()

    def sample_count(self, stage: str) -> int:
        """Number of samples accumulated for ``stage`` since the last flush."""
        return len(self._stage_samples.get(stage, []))
