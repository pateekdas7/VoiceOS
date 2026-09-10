"""DialerSessionManager — one asyncio task per campaign dial session.

Tracks running sessions so the BFF can start, stop, and query them.

Architecture: V5 Ch6 (Campaign Engine — Dialer, ADR-005 §15).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .engine import DialerEngine
from .queue import DialerQueue

_log = logging.getLogger("voiceos.dialer.session")


@dataclass
class DialerSessionInfo:
    tenant_id: str
    campaign_id: str
    started_at: datetime
    status: str = "running"  # running | stopping | stopped


class DialerSessionManager:
    """Manages per-campaign dial loop asyncio tasks.

    Args:
        engine_factory: Callable that returns a fresh DialerEngine for a campaign.
                        Injected so tests can stub the engine without real Twilio.
        dialer_queue:   Shared DialerQueue.
        lead_repo:      CampaignLeadRepository for seeding the queue.
    """

    def __init__(
        self,
        engine_factory: Any,  # () -> DialerEngine
        dialer_queue: DialerQueue,
        lead_repo: Any,
    ) -> None:
        self._factory = engine_factory
        self._queue = dialer_queue
        self._lead_repo = lead_repo
        self._engines: dict[str, DialerEngine] = {}  # campaign_id → engine
        self._tasks: dict[str, asyncio.Task] = {}    # campaign_id → task
        self._sessions: dict[str, DialerSessionInfo] = {}

    # ------------------------------------------------------------------
    # BFF control surface
    # ------------------------------------------------------------------

    async def start(
        self,
        tenant_id: str,
        campaign_id: str,
        *,
        daily_start_hour: int,
        daily_end_hour: int,
        timezone_name: str = "Asia/Kolkata",
    ) -> DialerSessionInfo:
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            return self._sessions[campaign_id]

        # Seed queue from PENDING leads
        pending = await asyncio.to_thread(
            self._lead_repo.find_queued_for_campaign,
            tenant_id, campaign_id,
        )
        if pending:
            leads_dicts = [
                {
                    "lead_id": str(lead.lead_id),
                    "phone": lead.phone,
                    "score": lead.score,
                    "name": lead.name,
                    "pipeline_id": str(lead.pipeline_id) if lead.pipeline_id else None,
                }
                for lead in pending
            ]
            await asyncio.to_thread(
                self._queue.push_leads, tenant_id, campaign_id, leads_dicts
            )
            _log.info("seeded queue tenant=%s campaign=%s count=%d", tenant_id, campaign_id, len(leads_dicts))

        engine = self._factory()
        self._engines[campaign_id] = engine

        task = asyncio.create_task(
            engine.run(
                tenant_id,
                campaign_id,
                daily_start_hour=daily_start_hour,
                daily_end_hour=daily_end_hour,
                timezone_name=timezone_name,
            ),
            name=f"dialer:{campaign_id}",
        )
        task.add_done_callback(lambda t: self._on_done(campaign_id, t))
        self._tasks[campaign_id] = task

        info = DialerSessionInfo(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            started_at=datetime.now(UTC),
        )
        self._sessions[campaign_id] = info
        _log.info("dialer session started campaign=%s", campaign_id)
        return info

    async def stop(self, campaign_id: str) -> None:
        engine = self._engines.get(campaign_id)
        if engine:
            engine.request_stop()
        info = self._sessions.get(campaign_id)
        if info:
            info.status = "stopping"

    def status(self, campaign_id: str) -> DialerSessionInfo | None:
        return self._sessions.get(campaign_id)

    def on_call_ended(self, call_sid: str, campaign_id: str) -> None:
        engine = self._engines.get(campaign_id)
        if engine:
            engine.on_call_ended(call_sid)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_done(self, campaign_id: str, task: asyncio.Task) -> None:
        info = self._sessions.get(campaign_id)
        if info:
            info.status = "stopped"
        exc = task.exception() if not task.cancelled() else None
        if exc:
            _log.error("dialer session crashed campaign=%s: %s", campaign_id, exc)
        else:
            _log.info("dialer session finished campaign=%s", campaign_id)
        self._engines.pop(campaign_id, None)
