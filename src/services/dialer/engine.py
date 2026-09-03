"""DialerEngine — async dial loop that runs while the campaign window is open.

Flow per iteration:
    1. Check current hour against campaign's daily_start_hour/daily_end_hour.
    2. While window open AND leads remain AND active_calls < max_concurrent:
       a. Pop highest-priority lead from DialerQueue.
       b. Place call via TwilioOutboundCallService.
       c. Store call_sid → lead_id mapping in Redis for status callback.
       d. Update lead queue_status to QUEUED.
       e. Increment active_calls Gauge.
    3. Sleep poll_interval_s and repeat.
    4. Stop when window closes or queue empty with no active calls.

Architecture: V5 Ch6 (Campaign Engine — Dialer, ADR-005 §15).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import metrics as _m
from .dnd import NullPhoneDND, PhoneDNDPort
from .outbound_call import CallPlacementError, TwilioOutboundCallService
from .queue import DialerQueue

_log = logging.getLogger("voiceos.dialer.engine")

# Redis key: call_sid → lead_id lookup used by the status callback
_CALL_SID_TTL = 86_400  # 24 h

# Fallback when a caller passes an unknown IANA timezone name — Asia/Kolkata
# is the compliance-relevant default (RBI FPC 8am–9pm applies in IST).
_DEFAULT_TZ = "Asia/Kolkata"

# Fixed IST offset used only when the system has no tzdata (e.g. Termux, some
# minimal CI images). IST does not observe DST so a fixed offset is correct
# for the compliance-relevant default; a Linux prod host with tzdata will hit
# the ZoneInfo path above and never see this branch.
_IST_FALLBACK = timezone(timedelta(hours=5, minutes=30), name="IST")


def _resolve_tz(name: str) -> tzinfo:
    """Return a tzinfo for ``name`` with a robust fallback when tzdata is absent."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in ("Asia/Kolkata", "Asia/Calcutta", "IST"):
            _log.warning("tzdata missing; using fixed IST offset for %r", name)
            return _IST_FALLBACK
        _log.warning("unknown timezone_name=%r; falling back to fixed IST offset", name)
        return _IST_FALLBACK


class DialerEngine:
    """Orchestrates the outbound dial loop for a single campaign session.

    Args:
        twilio:         Configured TwilioOutboundCallService.
        dialer_queue:   Shared DialerQueue instance.
        lead_repo:      CampaignLeadRepository for queue_status updates.
        redis:          Raw ``redis.Redis`` for call-SID tracking.
        max_concurrent: Maximum simultaneous in-flight calls.
        poll_interval_s: Seconds to sleep between queue-empty polls.
    """

    def __init__(
        self,
        twilio: TwilioOutboundCallService,
        dialer_queue: DialerQueue,
        lead_repo: Any,
        redis: Any,
        *,
        max_concurrent: int = 1,
        poll_interval_s: float = 2.0,
        dnd: PhoneDNDPort | None = None,
    ) -> None:
        self._twilio = twilio
        self._queue = dialer_queue
        self._lead_repo = lead_repo
        self._redis = redis
        self._max_concurrent = max_concurrent
        self._poll_interval_s = poll_interval_s
        # NullPhoneDND (never-blocks) rather than None so the dial-time
        # check is unconditional — this class already fails compliance
        # audits with a None-typed skip; forcing every branch to run the
        # port removes that whole class of bug at construction time.
        self._dnd: PhoneDNDPort = dnd or NullPhoneDND()
        self._active_calls: dict[str, str] = {}  # call_sid → lead_id
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal the dial loop to stop after the current iteration."""
        self._stop_event.set()

    async def run(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        daily_start_hour: int,
        daily_end_hour: int,
        timezone_name: str = _DEFAULT_TZ,
    ) -> None:
        """Drive the dial loop until the window closes or leads are exhausted.

        Window enforcement uses the campaign's local timezone (default IST) so
        that RBI FPC's 8am–9pm rule is honored regardless of the server clock.
        """
        tz = _resolve_tz(timezone_name)
        _log.info(
            "dialer session starting tenant=%s campaign=%s window=%02d:00-%02d:00 tz=%s",
            tenant_id, campaign_id, daily_start_hour, daily_end_hour, timezone_name,
        )
        _m.SESSIONS_ACTIVE.labels(tenant_id=tenant_id).inc()
        try:
            await self._loop(tenant_id, campaign_id, daily_start_hour, daily_end_hour, tz)
        finally:
            _m.SESSIONS_ACTIVE.labels(tenant_id=tenant_id).dec()
            _log.info("dialer session ended tenant=%s campaign=%s", tenant_id, campaign_id)

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(
        self,
        tenant_id: str,
        campaign_id: str,
        daily_start_hour: int,
        daily_end_hour: int,
        tz: tzinfo,
    ) -> None:
        tz_label = getattr(tz, "key", None) or tz.tzname(None) or str(tz)
        while not self._stop_event.is_set():
            now_hour = datetime.now(tz).hour

            if not (daily_start_hour <= now_hour < daily_end_hour):
                _log.info(
                    "dialer window closed (local_hour=%d tz=%s window=%02d:00-%02d:00) tenant=%s campaign=%s",
                    now_hour, tz_label, daily_start_hour, daily_end_hour, tenant_id, campaign_id,
                )
                break

            # Drain queue up to concurrency limit
            while len(self._active_calls) < self._max_concurrent:
                lead = await asyncio.to_thread(
                    self._queue.pop_next, tenant_id, campaign_id
                )
                if lead is None:
                    break  # Queue empty — wait for next poll or stop
                await self._place(lead, tenant_id, campaign_id)

            _m.QUEUE_DEPTH.labels(tenant_id=tenant_id, campaign_id=campaign_id).set(
                await asyncio.to_thread(self._queue.size, tenant_id, campaign_id)
            )
            _m.ACTIVE_CALLS.labels(tenant_id=tenant_id, campaign_id=campaign_id).set(
                len(self._active_calls)
            )

            queue_empty = await asyncio.to_thread(self._queue.is_empty, tenant_id, campaign_id)
            if queue_empty and not self._active_calls:
                _log.info(
                    "dialer queue exhausted tenant=%s campaign=%s", tenant_id, campaign_id
                )
                break

            await asyncio.sleep(self._poll_interval_s)

    async def _place(self, lead: dict[str, Any], tenant_id: str, campaign_id: str) -> None:
        lead_id = lead["lead_id"]
        phone = lead["phone"]

        if self._dnd.is_on_dnd(phone):
            _log.info(
                "call blocked by DND registry lead_id=%s phone=%s tenant=%s campaign=%s",
                lead_id, phone, tenant_id, campaign_id,
            )
            _m.record_call_blocked_dnd(tenant_id, campaign_id)
            await self._mark_done(tenant_id, lead_id)
            return

        try:
            call_sid = await self._twilio.place_call(
                phone,
                lead_id=lead_id,
                campaign_id=campaign_id,
                tenant_id=tenant_id,
            )
        except CallPlacementError as exc:
            _log.error("call placement failed lead_id=%s: %s", lead_id, exc)
            _m.record_call_failed(tenant_id, campaign_id)
            await self._mark_done(tenant_id, lead_id, failed=True)
            return

        # Track in-flight
        self._active_calls[call_sid] = lead_id
        self._redis.setex(
            f"dialer:sid:{call_sid}",
            _CALL_SID_TTL,
            f"{tenant_id}|{campaign_id}|{lead_id}",
        )
        await asyncio.to_thread(
            self._lead_repo.update_queue_status,
            tenant_id, lead_id, _queue_status("QUEUED"), call_sid,
        )
        _m.record_call_placed(tenant_id, campaign_id)
        _log.info("call placed call_sid=%s lead_id=%s phone=%s", call_sid, lead_id, phone)

    async def _mark_done(self, tenant_id: str, lead_id: str, *, failed: bool = False) -> None:
        await asyncio.to_thread(
            self._lead_repo.update_queue_status,
            tenant_id, lead_id, _queue_status("DONE"),
        )

    def on_call_ended(self, call_sid: str) -> None:
        """Called by the status callback handler when a call reaches a terminal state.

        Removes the call from the active set so the loop can place the next one.
        """
        self._active_calls.pop(call_sid, None)
        self._redis.delete(f"dialer:sid:{call_sid}")


def _queue_status(name: str) -> Any:
    from src.libs.contracts.models.pipeline import LeadQueueStatus
    return LeadQueueStatus(name)
