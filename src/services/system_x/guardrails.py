"""RecoveryGuardrails — circuit breaker and rate limits for System X.

Prevents System X from oscillating between restart and failure indefinitely.
All limits are in-memory (single process). A production multi-instance
deployment should back these counters in Redis — the env var
SYSTEM_X_USE_REDIS_GUARDRAILS enables that path (not yet wired; single-process
deployments do not need it).

Enforces:
  - Max automated recoveries per hour across all incidents
  - Max restart attempts per service within the cooldown window
  - Per-service cooldown period between recoveries
  - Incident deduplication (same alert fingerprint within dedup window)
  - Recovery timeout (action must complete within timeout_s)
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

_log = logging.getLogger("system_x.guardrails")

_MAX_RECOVERIES_PER_HOUR = 10
_MAX_RESTARTS_PER_SERVICE = 3       # within cooldown window
_COOLDOWN_S = 300                   # 5 minutes between recoveries for same service
_DEDUP_WINDOW_S = 120               # same fingerprint within 2 min = duplicate
_RECOVERY_TIMEOUT_S = 600           # 10 minutes max per recovery


@dataclass
class _ServiceRecord:
    last_recovery_ts: float = 0.0
    restart_count: int = 0
    restart_window_start: float = field(default_factory=time.monotonic)


class RecoveryGuardrails:
    """Stateful in-process circuit breaker for System X recovery.

    All public methods are synchronous (safe to call from async code since
    they're non-blocking in-memory operations).
    """

    def __init__(
        self,
        max_per_hour: int = _MAX_RECOVERIES_PER_HOUR,
        max_restarts_per_service: int = _MAX_RESTARTS_PER_SERVICE,
        cooldown_s: float = _COOLDOWN_S,
        dedup_window_s: float = _DEDUP_WINDOW_S,
        recovery_timeout_s: float = _RECOVERY_TIMEOUT_S,
    ) -> None:
        self._max_per_hour = max_per_hour
        self._max_restarts = max_restarts_per_service
        self._cooldown_s = cooldown_s
        self._dedup_window_s = dedup_window_s
        self._recovery_timeout_s = recovery_timeout_s

        # Sliding window: timestamps of recent recovery starts
        self._recent_recoveries: Deque[float] = deque()
        # Per-service state
        self._service_records: dict[str, _ServiceRecord] = defaultdict(_ServiceRecord)
        # Fingerprint → last seen timestamp for deduplication
        self._seen_fingerprints: dict[str, float] = {}
        # Active recovery start times (incident_id → start_ts)
        self._active_recoveries: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_can_recover(
        self,
        incident_id: str,
        affected_services: list[str],
        alert_fingerprints: list[str],
    ) -> tuple[bool, str]:
        """Return (allowed, reason). Call before starting any recovery."""
        now = time.monotonic()

        # 1. Deduplication: if all fingerprints were seen recently, skip
        if alert_fingerprints and all(
            now - self._seen_fingerprints.get(fp, 0) < self._dedup_window_s
            for fp in alert_fingerprints
        ):
            return False, f"Duplicate incident suppressed: all fingerprints seen within {self._dedup_window_s}s window"

        # 2. Global hourly rate limit
        self._prune_recent(now)
        if len(self._recent_recoveries) >= self._max_per_hour:
            oldest = self._recent_recoveries[0]
            wait = 3600 - (now - oldest)
            return False, f"Global recovery rate limit reached ({self._max_per_hour}/hour). Wait {wait:.0f}s."

        # 3. Per-service cooldown and restart limit
        for svc in affected_services:
            rec = self._service_records[svc]
            since_last = now - rec.last_recovery_ts
            if since_last < self._cooldown_s:
                wait = self._cooldown_s - since_last
                return False, f"Service '{svc}' in cooldown. {wait:.0f}s remaining."
            # Reset restart counter if cooldown window has passed
            if now - rec.restart_window_start > self._cooldown_s:
                rec.restart_count = 0
                rec.restart_window_start = now
            if rec.restart_count >= self._max_restarts:
                return False, (
                    f"Service '{svc}' has been restarted {rec.restart_count} times in the "
                    f"current window (max {self._max_restarts}). Manual intervention required."
                )

        return True, "allowed"

    def check_timeout(self, incident_id: str) -> tuple[bool, str]:
        """Return (timed_out, message). Call during recovery to enforce timeout."""
        start = self._active_recoveries.get(incident_id)
        if start is None:
            return False, "no active recovery tracked"
        elapsed = time.monotonic() - start
        if elapsed > self._recovery_timeout_s:
            return True, f"Recovery timeout exceeded ({elapsed:.0f}s > {self._recovery_timeout_s}s)"
        return False, f"elapsed {elapsed:.0f}s"

    def record_start(self, incident_id: str, affected_services: list[str], fingerprints: list[str]) -> None:
        """Record that a recovery has started."""
        now = time.monotonic()
        self._recent_recoveries.append(now)
        self._active_recoveries[incident_id] = now
        for fp in fingerprints:
            self._seen_fingerprints[fp] = now
        for svc in affected_services:
            rec = self._service_records[svc]
            rec.last_recovery_ts = now
            rec.restart_count += 1

    def record_complete(self, incident_id: str) -> None:
        """Record that a recovery has completed (success or fail)."""
        self._active_recoveries.pop(incident_id, None)

    def is_incident_duplicate(self, fingerprints: list[str]) -> bool:
        """True if all fingerprints were recently seen (dedup check)."""
        now = time.monotonic()
        return bool(fingerprints) and all(
            now - self._seen_fingerprints.get(fp, 0) < self._dedup_window_s
            for fp in fingerprints
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _prune_recent(self, now: float) -> None:
        """Remove recovery timestamps older than 1 hour from the sliding window."""
        cutoff = now - 3600
        while self._recent_recoveries and self._recent_recoveries[0] < cutoff:
            self._recent_recoveries.popleft()


__all__ = ["RecoveryGuardrails"]
