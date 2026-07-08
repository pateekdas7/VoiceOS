"""LoadShedder — drop lowest-priority work first under sustained overload.

Per V3 Ch9 §9.12: "under sustained overload, the shedder drops
speculative/low-priority work first" — never in-flight/high-priority work,
and never a crash. This is the admission-time counterpart to
:class:`~src.libs.concurrency.worker_pool.WorkerPool`'s dispatch-time
priority ordering.

Architecture: V3 Ch9 §9.12, §9.16; V3 Ch14 §14.12 (load shedding).
"""

from __future__ import annotations

from src.libs.concurrency.worker_pool import Priority

DEFAULT_LOAD_THRESHOLD = 0.8
"""Default load fraction (0.0-1.0) above which shedding begins."""


class LoadShedder:
    """Decides whether to admit new work under a given system load.

    Shedding order (least valuable first): SPECULATIVE -> LOW -> NORMAL.
    HIGH-priority work is never shed — it is the floor V3 Ch9 §9.16
    guarantees ("never starve in-flight calls").
    """

    def __init__(self, threshold: float = DEFAULT_LOAD_THRESHOLD) -> None:
        """
        Args:
            threshold: Load fraction (0.0-1.0) above which shedding activates.

        Raises:
            ValueError: If ``threshold`` is not in [0.0, 1.0].
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"LoadShedder: threshold must be in [0.0, 1.0], got {threshold}")
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        """The configured shedding threshold."""
        return self._threshold

    def should_shed(self, current_load: float, priority: Priority) -> bool:
        """Decide whether to shed (reject) work at ``priority`` given ``current_load``.

        Args:
            current_load: Current system load as a fraction (0.0-1.0+; values
                above 1.0 represent overload beyond nominal capacity).
            priority: The priority of the work being admitted.

        Returns:
            True if this work should be shed (rejected), False if it should be admitted.
        """
        if current_load <= self._threshold:
            return False
        if priority == Priority.HIGH:
            return False
        if priority == Priority.SPECULATIVE:
            return True
        if priority == Priority.LOW:
            return True
        # NORMAL: only shed once load is significantly past threshold.
        return current_load >= 1.0

    def admit(self, current_load: float, priority: Priority) -> bool:
        """Inverse of :meth:`should_shed` — True if the work should be admitted."""
        return not self.should_shed(current_load, priority)
