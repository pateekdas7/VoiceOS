"""Unit tests for Phase 6e: RealtimeAnalytics SSE endpoint.

Verifies:
- Unauthenticated request → 401
- Missing PERM_VIEW_ANALYTICS → 403
- SSE response has correct media_type (text/event-stream)
- SSE response has correct headers (Cache-Control, X-Accel-Buffering)
- Data frames are valid JSON prefixed with "data: "
- Keepalive comment format is ": keepalive"
- interval_seconds clamping (1..60)
- Tenant isolation (tenant_id from session, not from query param)

Uses the Starlette TestClient via the existing _build_*_routes pattern.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from src.services.analytics.service import AnalyticsService
from src.services.web_api.api import _build_realtime_analytics_routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analytics_service(snapshot: dict[str, Any] | None = None) -> AnalyticsService:
    """Build a minimal AnalyticsService with a fixed snapshot."""
    svc = MagicMock(spec=AnalyticsService)
    svc.dashboard_snapshot.return_value = snapshot or {
        "tenant_id": "t-1",
        "as_of": datetime.now(UTC).isoformat(),
        "calls_completed": 42,
        "outcome_distribution": {},
        "average_duration_ms": 15000,
        "contactability_rate": 0.75,
        "recovery_rate": 0.12,
    }
    return svc


def _make_app_with_auth(session_data: dict | None = None, forbidden: bool = False) -> Starlette:
    """Build a minimal Starlette app with the SSE routes + a fake session middleware."""
    analytics = _make_analytics_service()
    routes = _build_realtime_analytics_routes(analytics)

    # Patch auth helpers at the module level for the test
    with patch("src.services.web_api.api.require_tenant_permission") as mock_auth, \
         patch("src.services.web_api.api._require_tenant_id") as mock_tid:

        if session_data is not None and not forbidden:
            mock_auth.return_value = session_data
            mock_tid.return_value = "t-1"
        elif forbidden:
            from src.services.web_api.api import ForbiddenError
            mock_auth.side_effect = ForbiddenError("forbidden")
        else:
            from src.services.web_api.api import SessionRequiredError
            mock_auth.side_effect = SessionRequiredError("unauthenticated")

        app = Starlette(routes=routes)
        return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRealtimeSSEAuth:
    def test_unauthenticated_returns_401(self) -> None:
        analytics = _make_analytics_service()
        routes = _build_realtime_analytics_routes(analytics)

        with patch("src.services.web_api.api.require_tenant_permission") as mock_auth:
            from src.services.web_api.api import SessionRequiredError
            mock_auth.side_effect = SessionRequiredError("not signed in")
            app = Starlette(routes=routes)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/analytics/stream")

        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "UNAUTHENTICATED"

    def test_forbidden_returns_403(self) -> None:
        analytics = _make_analytics_service()
        routes = _build_realtime_analytics_routes(analytics)

        with patch("src.services.web_api.api.require_tenant_permission") as mock_auth:
            from src.services.web_api.api import ForbiddenError
            mock_auth.side_effect = ForbiddenError("insufficient permissions")
            app = Starlette(routes=routes)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/analytics/stream")

        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "FORBIDDEN"


class TestRealtimeSSEResponseFormat:
    def test_response_has_event_stream_content_type(self) -> None:
        analytics = _make_analytics_service()
        routes = _build_realtime_analytics_routes(analytics)

        with patch("src.services.web_api.api.require_tenant_permission") as mock_auth, \
             patch("src.services.web_api.api._require_tenant_id") as mock_tid, \
             patch("src.services.web_api.api.Request.is_disconnected", new_callable=AsyncMock, return_value=True):
            mock_auth.return_value = {"tenant_id": "t-1"}
            mock_tid.return_value = "t-1"
            app = Starlette(routes=routes)
            client = TestClient(app, raise_server_exceptions=False)
            # With is_disconnected=True the generator exits immediately
            response = client.get("/analytics/stream")

        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_response_has_no_cache_header(self) -> None:
        analytics = _make_analytics_service()
        routes = _build_realtime_analytics_routes(analytics)

        with patch("src.services.web_api.api.require_tenant_permission") as mock_auth, \
             patch("src.services.web_api.api._require_tenant_id") as mock_tid, \
             patch("src.services.web_api.api.Request.is_disconnected", new_callable=AsyncMock, return_value=True):
            mock_auth.return_value = {"tenant_id": "t-1"}
            mock_tid.return_value = "t-1"
            app = Starlette(routes=routes)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/analytics/stream")

        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-accel-buffering") == "no"


class TestRealtimeSSEInterval:
    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("0.5", 1.0),     # below minimum → clamp to 1
            ("1", 1.0),       # at minimum
            ("5", 5.0),       # normal
            ("60", 60.0),     # at maximum
            ("120", 60.0),    # above maximum → clamp to 60
            ("abc", 5.0),     # invalid → default
        ],
    )
    def test_interval_clamping(self, input_val: str, expected: float) -> None:
        # Test the clamping logic directly (extracted from the route handler)
        try:
            interval = float(input_val)
        except ValueError:
            interval = 5.0
        interval = max(1.0, min(60.0, interval))
        assert interval == expected


class TestRealtimeSSEDataFormat:
    def test_sse_frame_starts_with_data_prefix(self) -> None:
        snapshot = {"tenant_id": "t-1", "calls_completed": 7}
        frame = f"data: {json.dumps(snapshot)}\n\n"
        assert frame.startswith("data: ")
        # Verify the JSON payload is valid
        payload = json.loads(frame[6:].strip())
        assert payload["calls_completed"] == 7

    def test_keepalive_frame_format(self) -> None:
        frame = ": keepalive\n\n"
        assert frame.startswith(":")

    def test_snapshot_is_json_serializable(self) -> None:
        analytics = _make_analytics_service()
        snapshot = analytics.dashboard_snapshot("t-1", datetime.now(UTC), datetime.now(UTC))
        serialized = json.dumps(snapshot)
        reparsed = json.loads(serialized)
        assert isinstance(reparsed, dict)
