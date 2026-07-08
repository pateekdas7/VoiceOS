"""Unit tests for IdempotencyGuard, IdempotencyKeyBuilder, and FencingToken (V3 Ch8)."""

from __future__ import annotations

import json

from src.libs.contracts.primitives import CallId, TenantId
from src.libs.idempotency.fencing import FencingToken, FencingTokenTracker
from src.libs.idempotency.guard import IdempotencyGuard
from src.libs.idempotency.key_builder import IdempotencyKeyBuilder
from src.libs.repositories.idempotency import IdempotencyRepository
from tests.fixtures.fake_pg import FakeConnection, FakeCursor


class TestIdempotencyKeyBuilder:
    def test_build_is_deterministic(self) -> None:
        key1 = IdempotencyKeyBuilder.build(CallId("call-1"), "turn-1", "ptp_create")
        key2 = IdempotencyKeyBuilder.build(CallId("call-1"), "turn-1", "ptp_create")

        assert key1 == key2 == "call-1:turn-1:ptp_create"

    def test_build_differs_by_effect_name(self) -> None:
        key1 = IdempotencyKeyBuilder.build(CallId("call-1"), "turn-1", "ptp_create")
        key2 = IdempotencyKeyBuilder.build(CallId("call-1"), "turn-1", "sms_send")

        assert key1 != key2


class TestFencingToken:
    def test_fencing_token_rejects_stale(self) -> None:
        """Required Sprint-015 test: token 5 after token 8 seen -> rejected."""
        assert FencingToken.validate(token=5, last_seen=8) is False

    def test_fencing_token_accepts_fresh(self) -> None:
        assert FencingToken.validate(token=9, last_seen=8) is True

    def test_fencing_token_accepts_equal(self) -> None:
        assert FencingToken.validate(token=8, last_seen=8) is True


class TestFencingTokenTracker:
    def test_rejects_stale_write_after_newer_lock(self) -> None:
        tracker = FencingTokenTracker()

        assert tracker.validate("call:abc", token=8) is True
        assert tracker.validate("call:abc", token=5) is False  # stale — delayed writer

    def test_accepts_monotonically_increasing_tokens(self) -> None:
        tracker = FencingTokenTracker()

        assert tracker.validate("call:abc", token=1) is True
        assert tracker.validate("call:abc", token=2) is True
        assert tracker.validate("call:abc", token=3) is True

    def test_tracks_resources_independently(self) -> None:
        tracker = FencingTokenTracker()

        assert tracker.validate("call:abc", token=10) is True
        assert tracker.validate("call:xyz", token=1) is True  # unrelated resource, unaffected


class TestIdempotencyGuardExecuteOnce:
    async def test_idempotency_second_call_returns_cached(self) -> None:
        """Required Sprint-015 test: same key twice -> effect_fn called once, cached result returned twice."""
        cursor = FakeCursor(
            fetchall_results=[[], [(json.dumps({"n": 1}),)]],
            fetchone_results=[("key-1",)],
        )
        repo = IdempotencyRepository(FakeConnection(cursor))
        guard = IdempotencyGuard(repo)
        tenant_id = TenantId("tenant-a")

        call_count = 0

        async def effect_fn() -> dict[str, int]:
            nonlocal call_count
            call_count += 1
            return {"n": 1}

        first = await guard.execute_once(tenant_id, "key-1", "ptp", effect_fn)
        second = await guard.execute_once(tenant_id, "key-1", "ptp", effect_fn)

        assert first == {"n": 1}
        assert second == {"n": 1}
        assert call_count == 1

    async def test_loser_polls_until_winner_completes(self) -> None:
        """A caller that loses the claim race polls check() until the result appears."""
        cursor = FakeCursor(
            # check() (miss) -> claim() (lost) -> check() (miss, still pending) -> check() (hit)
            fetchall_results=[[], [], [(json.dumps({"ptp_id": "ptp-1"}),)]],
            fetchone_results=[None],
        )
        repo = IdempotencyRepository(FakeConnection(cursor))
        guard = IdempotencyGuard(repo, poll_interval_seconds=0.0, poll_timeout_seconds=1.0)

        async def effect_fn() -> dict[str, str]:
            raise AssertionError("effect_fn must not run for the losing caller")

        result = await guard.execute_once(TenantId("tenant-a"), "key-1", "ptp", effect_fn)

        assert result == {"ptp_id": "ptp-1"}
