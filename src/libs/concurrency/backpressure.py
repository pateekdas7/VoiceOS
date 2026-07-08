"""BackpressureSignal — emitted when a downstream queue crosses its high-water mark.

Per V3 Ch9 §9.11/§9.12: when a bounded queue exceeds its high-water ratio
(default 80% full), upstream producers must slow or pause (the canonical
example is playback backpressure pausing the LLM token pull, V1 Ch18).
``BackpressureMonitor`` computes this signal from a queue's current depth.

Architecture: V3 Ch9 §9.11 (backpressure sequence), §9.12 (RI-3 backpressure).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.libs.concurrency.bounded_queue import BoundedQueue

DEFAULT_HIGH_WATER_RATIO = 0.8
"""Default fill ratio (0.0-1.0) above which backpressure activates."""


@dataclass(frozen=True)
class BackpressureSignal:
    """The result of a backpressure check against a bounded queue."""

    queue_name: str
    fill_ratio: float
    active: bool
    """True if producers should slow down (queue is above the high-water mark)."""


class BackpressureMonitor:
    """Computes :class:`BackpressureSignal` for a queue's current depth."""

    def __init__(self, high_water_ratio: float = DEFAULT_HIGH_WATER_RATIO) -> None:
        """
        Args:
            high_water_ratio: Fill ratio (0.0-1.0) above which backpressure activates.

        Raises:
            ValueError: If ``high_water_ratio`` is not in [0.0, 1.0].
        """
        if not 0.0 <= high_water_ratio <= 1.0:
            raise ValueError(f"BackpressureMonitor: high_water_ratio must be in [0.0, 1.0], got {high_water_ratio}")
        self._high_water_ratio = high_water_ratio

    @property
    def high_water_ratio(self) -> float:
        """The configured high-water ratio."""
        return self._high_water_ratio

    def signal[T](self, queue: BoundedQueue[T]) -> BackpressureSignal:
        """Compute the current backpressure signal for ``queue``."""
        fill_ratio = queue.fill_ratio()
        return BackpressureSignal(
            queue_name=queue.name,
            fill_ratio=fill_ratio,
            active=fill_ratio > self._high_water_ratio,
        )

    def signal_for(self, queue_name: str, current_size: int, max_size: int) -> BackpressureSignal:
        """Compute a backpressure signal from raw depth/capacity (no BoundedQueue needed)."""
        fill_ratio = current_size / max_size
        return BackpressureSignal(
            queue_name=queue_name,
            fill_ratio=fill_ratio,
            active=fill_ratio > self._high_water_ratio,
        )
