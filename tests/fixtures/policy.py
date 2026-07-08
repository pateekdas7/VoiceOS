"""FakePolicyEngineService — deterministic RBI calling-hours/frequency PDP double (Sprint-023).

Matches Sprint-023.md's Phase 1 Mock Backends table: "PolicyEngine |
FakePolicyEngine | Returns DENY for outside-hours requests; PERMIT for
others". Named ``FakePolicyEngineService`` (matching the real
``PolicyEngineService`` class it substitutes for) rather than
``FakePolicyEngine`` verbatim, consistent with every other fixture in this
directory naming after the concrete class it fakes.
"""

from __future__ import annotations

from src.services.policy_engine.decision import PolicyDecision, PolicyOutcome
from src.services.policy_engine.packs.rbi import CALLING_WINDOW_END_HOUR, CALLING_WINDOW_START_HOUR, MAX_CALLS_PER_DAY


class FakePolicyEngineService:
    """Deterministic double for ``PolicyEngineService.check_call_admission()``.

    Applies the same RBI thresholds as the real ``RBIPolicyPack``
    (08:00-20:00, max 3 calls/day) by default, with no Postgres/Redis
    dependency — suitable for ``ScheduleEngine`` unit tests.
    """

    def __init__(
        self,
        calling_window: tuple[int, int] = (CALLING_WINDOW_START_HOUR, CALLING_WINDOW_END_HOUR),
        max_calls_per_day: int = MAX_CALLS_PER_DAY,
    ) -> None:
        self._start, self._end = calling_window
        self._max_calls_per_day = max_calls_per_day
        self.calls: list[dict[str, object]] = []

    def check_call_admission(
        self,
        tenant_id: str,
        call_id: str,
        subject: str = "conversation_engine",
        hour: int | None = None,
        calls_today_count: int = 0,
    ) -> PolicyDecision:
        self.calls.append(
            {"tenant_id": tenant_id, "call_id": call_id, "hour": hour, "calls_today_count": calls_today_count}
        )
        effective_hour = hour if hour is not None else 12

        if calls_today_count >= self._max_calls_per_day:
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                matching_rules=("RBI-CALLING-FREQUENCY",),
                reason=f"calls_today_count {calls_today_count} >= {self._max_calls_per_day}",
            )
        if not (self._start <= effective_hour < self._end):
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                matching_rules=("RBI-CALLING-HOURS",),
                reason=f"hour {effective_hour} outside {self._start:02d}:00-{self._end:02d}:00",
            )
        return PolicyDecision(outcome=PolicyOutcome.PERMIT, reason="within calling hours and frequency cap")
