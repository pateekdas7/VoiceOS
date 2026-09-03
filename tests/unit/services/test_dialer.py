"""Unit tests for the Dialer system.

Covers DialerQueue, DialerEngine, and TwilioOutboundCallService using
in-memory doubles — no real Redis, no real Twilio.

Architecture: V5 Ch6 (Campaign Engine — Dialer, ADR-005 §15).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.dialer.dnd import CsvPhoneDNDList, PhoneDNDPort
from src.services.dialer.engine import DialerEngine
from src.services.dialer.outbound_call import CallPlacementError, TwilioOutboundCallService
from src.services.dialer.queue import DialerQueue

TENANT = "t-001"
CAMPAIGN = "c-001"


# ─────────────────────────────────────────────────────────────────────────────
# Fake Redis (sorted-set + setex + get + delete)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self._zsets: dict[str, dict[str, float]] = {}
        self._store: dict[str, bytes] = {}

    # Sorted set
    def zadd(self, key: str, mapping: dict[str, float], **kwargs: Any) -> int:
        nx = kwargs.get("nx", False)
        added = 0
        zset = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            if nx and member in zset:
                continue
            zset[member] = score
            added += 1
        return added

    def zpopmin(self, key: str, count: int = 1) -> list[tuple[bytes, float]]:
        zset = self._zsets.get(key, {})
        if not zset:
            return []
        # lowest score first (most negative = highest priority lead)
        sorted_items = sorted(zset.items(), key=lambda kv: kv[1])
        result = []
        for member, score in sorted_items[:count]:
            del zset[member]
            result.append((member.encode() if isinstance(member, str) else member, score))
        return result

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def expire(self, key: str, ttl: int) -> None:
        pass

    def delete(self, *keys: str) -> None:
        for k in keys:
            self._zsets.pop(k, None)
            self._store.pop(k, None)

    # String ops for call-SID tracking
    def setex(self, key: str, ttl: int, value: Any) -> None:
        self._store[key] = value.encode() if isinstance(value, str) else value

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)


# ─────────────────────────────────────────────────────────────────────────────
# Fake lead repository
# ─────────────────────────────────────────────────────────────────────────────


class _FakeLeadRepo:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_queue_status(
        self,
        tenant_id: str,
        lead_id: str,
        queue_status: Any,
        call_sid: str | None = None,
    ) -> None:
        self.updates.append({"lead_id": lead_id, "status": queue_status, "call_sid": call_sid})

    def find_queued_for_campaign(self, tenant_id: str, campaign_id: str) -> tuple:
        return ()


# ─────────────────────────────────────────────────────────────────────────────
# DialerQueue tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDialerQueue:
    def _queue(self) -> tuple[DialerQueue, _FakeRedis]:
        redis = _FakeRedis()
        return DialerQueue(redis), redis

    def test_push_returns_count(self) -> None:
        q, _ = self._queue()
        leads = [{"lead_id": "l1", "phone": "+919876543210", "score": 80}]
        assert q.push_leads(TENANT, CAMPAIGN, leads) == 1

    def test_empty_push_returns_zero(self) -> None:
        q, _ = self._queue()
        assert q.push_leads(TENANT, CAMPAIGN, []) == 0

    def test_pop_returns_highest_score_first(self) -> None:
        q, _ = self._queue()
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-low", "phone": "+919876543210", "score": 20},
            {"lead_id": "l-high", "phone": "+919876543211", "score": 80},
            {"lead_id": "l-mid", "phone": "+919876543212", "score": 50},
        ])
        first = q.pop_next(TENANT, CAMPAIGN)
        assert first is not None
        assert first["lead_id"] == "l-high"

    def test_pop_empty_returns_none(self) -> None:
        q, _ = self._queue()
        assert q.pop_next(TENANT, CAMPAIGN) is None

    def test_size_and_is_empty(self) -> None:
        q, _ = self._queue()
        assert q.is_empty(TENANT, CAMPAIGN)
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l1", "phone": "+919876543210", "score": 50}])
        assert q.size(TENANT, CAMPAIGN) == 1
        assert not q.is_empty(TENANT, CAMPAIGN)

    def test_clear_empties_queue(self) -> None:
        q, _ = self._queue()
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l1", "phone": "+919876543210", "score": 50}])
        q.clear(TENANT, CAMPAIGN)
        assert q.is_empty(TENANT, CAMPAIGN)

    def test_nx_semantics_no_duplicate_push(self) -> None:
        q, _ = self._queue()
        lead = {"lead_id": "l1", "phone": "+919876543210", "score": 50}
        q.push_leads(TENANT, CAMPAIGN, [lead])
        # Second push of same lead — NX should skip it
        # (member string is deterministic from lead_id+phone so same member)
        q.push_leads(TENANT, CAMPAIGN, [lead])
        assert q.size(TENANT, CAMPAIGN) == 1

    def test_pop_preserves_lead_id(self) -> None:
        q, _ = self._queue()
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l-abc", "phone": "+919876543210", "score": 60}])
        lead = q.pop_next(TENANT, CAMPAIGN)
        assert lead is not None
        assert lead["lead_id"] == "l-abc"
        assert lead["phone"] == "+919876543210"
        assert lead["tenant_id"] == TENANT
        assert lead["campaign_id"] == CAMPAIGN


# ─────────────────────────────────────────────────────────────────────────────
# TwilioOutboundCallService tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTwilioOutboundCallService:
    def _svc(self) -> TwilioOutboundCallService:
        return TwilioOutboundCallService(
            account_sid="ACtest",
            auth_token="token",
            caller_id="+911234567890",
            twiml_app_url="https://example.com/twiml",
            status_callback_url="https://example.com/status",
        )

    @pytest.mark.asyncio
    async def test_place_call_returns_sid(self) -> None:
        svc = self._svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"sid": "CA123abc"}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            sid = await svc.place_call("+919876543210", lead_id="l1", campaign_id="c1", tenant_id="t1")

        assert sid == "CA123abc"

    @pytest.mark.asyncio
    async def test_place_call_raises_on_4xx(self) -> None:
        svc = self._svc()
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            with pytest.raises(CallPlacementError, match="400"):
                await svc.place_call("+919876543210", lead_id="l1", campaign_id="c1", tenant_id="t1")

    @pytest.mark.asyncio
    async def test_place_call_raises_on_network_error(self) -> None:
        import httpx

        svc = self._svc()
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            mock_cls.return_value = mock_client

            with pytest.raises(CallPlacementError, match="network error"):
                await svc.place_call("+919876543210", lead_id="l1", campaign_id="c1", tenant_id="t1")


# ─────────────────────────────────────────────────────────────────────────────
# DialerEngine tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_engine(
    twilio: Any | None = None,
    queue: DialerQueue | None = None,
    lead_repo: Any | None = None,
    redis: Any | None = None,
    max_concurrent: int = 1,
    poll_interval_s: float = 0.01,
    dnd: PhoneDNDPort | None = None,
) -> tuple[DialerEngine, DialerQueue, _FakeLeadRepo, _FakeRedis]:
    r = redis or _FakeRedis()
    q = queue or DialerQueue(r)
    repo = lead_repo or _FakeLeadRepo()
    if twilio is None:
        twilio = AsyncMock(spec=TwilioOutboundCallService)
        twilio.place_call = AsyncMock(return_value="CA999")
    engine = DialerEngine(
        twilio=twilio,
        dialer_queue=q,
        lead_repo=repo,
        redis=r,
        max_concurrent=max_concurrent,
        poll_interval_s=poll_interval_s,
        dnd=dnd,
    )
    return engine, q, repo, r


class TestDialerEngine:
    @pytest.mark.asyncio
    async def test_stops_when_queue_empty(self) -> None:
        """Engine exits cleanly with no leads."""
        engine, q, repo, _ = _make_engine()
        # No leads pushed → loop exits immediately
        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)
        assert len(repo.updates) == 0

    @pytest.mark.asyncio
    async def test_places_call_for_lead(self) -> None:
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(return_value="CA_test_sid")

        engine, q, repo, redis = _make_engine(twilio=mock_twilio)
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l1", "phone": "+919876543210", "score": 70}])

        # Simulate call ending after 1 poll by patching the loop
        original_place = engine._place

        async def _place_and_end(lead: Any, tenant_id: str, campaign_id: str) -> None:
            await original_place(lead, tenant_id, campaign_id)
            # Signal call ended so engine can finish
            engine.on_call_ended("CA_test_sid")

        engine._place = _place_and_end  # type: ignore[method-assign]
        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        mock_twilio.place_call.assert_awaited_once()
        call_args = mock_twilio.place_call.call_args
        assert call_args.args[0] == "+919876543210"
        assert call_args.kwargs["lead_id"] == "l1"

    @pytest.mark.asyncio
    async def test_queue_status_set_to_queued(self) -> None:
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(return_value="CA_sid_q")

        engine, q, repo, _ = _make_engine(twilio=mock_twilio)
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l-queue-test", "phone": "+919876543210", "score": 60}])

        original_place = engine._place

        async def _place_and_end(lead: Any, tenant_id: str, campaign_id: str) -> None:
            await original_place(lead, tenant_id, campaign_id)
            engine.on_call_ended("CA_sid_q")

        engine._place = _place_and_end  # type: ignore[method-assign]
        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        queued_updates = [u for u in repo.updates if str(u["status"]) == "QUEUED"]
        assert len(queued_updates) == 1
        assert queued_updates[0]["lead_id"] == "l-queue-test"
        assert queued_updates[0]["call_sid"] == "CA_sid_q"

    @pytest.mark.asyncio
    async def test_failed_placement_marks_lead_done(self) -> None:
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(side_effect=CallPlacementError("Twilio 400"))

        engine, q, repo, _ = _make_engine(twilio=mock_twilio)
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l-fail", "phone": "+919876543210", "score": 50}])

        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        done_updates = [u for u in repo.updates if str(u["status"]) == "DONE"]
        assert len(done_updates) == 1
        assert done_updates[0]["lead_id"] == "l-fail"

    @pytest.mark.asyncio
    async def test_window_check_stops_outside_hours(self) -> None:
        """If current hour is outside window the engine must not place any calls."""
        # Force an impossible window: start=23, end=23 (empty range)
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        engine, q, _, _ = _make_engine(twilio=mock_twilio)
        q.push_leads(TENANT, CAMPAIGN, [{"lead_id": "l1", "phone": "+919876543210", "score": 50}])

        await engine.run(TENANT, CAMPAIGN, daily_start_hour=23, daily_end_hour=23)

        mock_twilio.place_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_request_stop_stops_loop(self) -> None:
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        call_count = 0

        async def _slow_place(*a: Any, **kw: Any) -> str:
            nonlocal call_count
            call_count += 1
            return f"CA_stop_{call_count}"

        mock_twilio.place_call = _slow_place

        engine, q, _, _ = _make_engine(twilio=mock_twilio, poll_interval_s=0.05)
        # Push many leads
        for i in range(10):
            q.push_leads(TENANT, CAMPAIGN, [{"lead_id": f"l{i}", "phone": f"+9198765432{i:02d}", "score": 50}])

        async def _stop_after_one() -> None:
            await asyncio.sleep(0.02)
            engine.request_stop()

        await asyncio.gather(
            engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23),
            _stop_after_one(),
        )
        # At most 1 call placed before stop signal processed
        assert call_count <= 2

    def test_on_call_ended_removes_from_active(self) -> None:
        engine, _, _, _ = _make_engine()
        engine._active_calls["CA_x"] = "l1"
        engine.on_call_ended("CA_x")
        assert "CA_x" not in engine._active_calls

    def test_on_call_ended_unknown_sid_noop(self) -> None:
        engine, _, _, _ = _make_engine()
        engine.on_call_ended("CA_unknown")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# DialerEngine DND enforcement tests (BLOCKER #2 — RBI FPC / TRAI NDNC)
# ─────────────────────────────────────────────────────────────────────────────


class TestDialerEngineDND:
    """The Dialer must consult a phone-scoped DND registry immediately
    before it hands a number to Twilio and abort the placement if the
    number matches. Consent-derived DND (``DNDStatusPort``, customer-id
    keyed) is a distinct, earlier check — this port is the regulatory
    tripwire that catches phone-scoped NDNC/NCCM registry hits."""

    @pytest.mark.asyncio
    async def test_dnd_hit_blocks_call_and_marks_done(self) -> None:
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(return_value="CA_never")

        dnd = CsvPhoneDNDList(["+919876543210"])
        engine, q, repo, _ = _make_engine(twilio=mock_twilio, dnd=dnd)
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-blocked", "phone": "+919876543210", "score": 90},
        ])

        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        mock_twilio.place_call.assert_not_awaited()
        done = [u for u in repo.updates if str(u["status"]) == "DONE"]
        assert len(done) == 1
        assert done[0]["lead_id"] == "l-blocked"

    @pytest.mark.asyncio
    async def test_dnd_hit_matches_via_normalisation(self) -> None:
        """DND list stores '+919876543210' but the lead's phone arrived
        as '+91 98765 43210' — the port's normaliser must still match."""
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        dnd = CsvPhoneDNDList(["+919876543210"])
        engine, q, _, _ = _make_engine(twilio=mock_twilio, dnd=dnd)
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-fmt", "phone": "+91 98765 43210", "score": 50},
        ])

        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        mock_twilio.place_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_dnd_phone_still_placed(self) -> None:
        """Regression: an engine with a non-trivial DND list must still
        place calls for numbers NOT on the list — otherwise the port
        silently blocks everything and the campaign runs dead."""
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(return_value="CA_ok")

        dnd = CsvPhoneDNDList(["+919999999999"])  # different number
        engine, q, repo, _ = _make_engine(twilio=mock_twilio, dnd=dnd)
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-ok", "phone": "+919876543210", "score": 50},
        ])

        original_place = engine._place

        async def _place_and_end(lead: Any, tenant_id: str, campaign_id: str) -> None:
            await original_place(lead, tenant_id, campaign_id)
            engine.on_call_ended("CA_ok")

        engine._place = _place_and_end  # type: ignore[method-assign]
        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        mock_twilio.place_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_dnd_is_null_port_never_blocks(self) -> None:
        """Constructing DialerEngine without ``dnd=`` must not silently
        block every call — the null-port default is what dev/test relies
        on. This test locks the default behavior."""
        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        mock_twilio.place_call = AsyncMock(return_value="CA_default")

        engine, q, _, _ = _make_engine(twilio=mock_twilio)  # dnd=None → NullPhoneDND
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-default", "phone": "+919876543210", "score": 50},
        ])

        original_place = engine._place

        async def _place_and_end(lead: Any, tenant_id: str, campaign_id: str) -> None:
            await original_place(lead, tenant_id, campaign_id)
            engine.on_call_ended("CA_default")

        engine._place = _place_and_end  # type: ignore[method-assign]
        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        mock_twilio.place_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dnd_block_increments_metric(self) -> None:
        """The CALLS_BLOCKED_DND counter is the compliance-relevant signal
        surfaced to Prometheus — a rising rate is what tells ops the
        upstream audience selector isn't pre-filtering DND correctly."""
        from src.services.dialer import metrics as _m

        before = _m.CALLS_BLOCKED_DND.labels(
            tenant_id=TENANT, campaign_id=CAMPAIGN
        )._value.get()

        mock_twilio = AsyncMock(spec=TwilioOutboundCallService)
        dnd = CsvPhoneDNDList(["+919876543210"])
        engine, q, _, _ = _make_engine(twilio=mock_twilio, dnd=dnd)
        q.push_leads(TENANT, CAMPAIGN, [
            {"lead_id": "l-metric", "phone": "+919876543210", "score": 50},
        ])

        await engine.run(TENANT, CAMPAIGN, daily_start_hour=0, daily_end_hour=23)

        after = _m.CALLS_BLOCKED_DND.labels(
            tenant_id=TENANT, campaign_id=CAMPAIGN
        )._value.get()
        assert after - before == 1
