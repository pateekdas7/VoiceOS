"""Route-level tests for /health, /health/live, /health/ready on the CPU
media-gateway app (V3 Ch12 §12.6, §12.9).

These exercise the real Starlette routes built by
``create_twilio_media_stream_app`` — the health handlers themselves,
without opening a WebSocket or invoking any real STT/LLM/TTS adapter.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from starlette.testclient import TestClient

from src.services.media_gateway.twilio_ws_entrypoint import (
    SharedCallDependencies,
    create_twilio_media_stream_app,
)


def _make_deps() -> SharedCallDependencies:
    return SharedCallDependencies(
        account_sid="ACtest",
        auth_token="tok",
        tenant_id="tenant-1",
        media_gateway_service=MagicMock(),
        audio_session_manager_service=MagicMock(),
        audio_preprocessor=MagicMock(),
        stt_service=MagicMock(),
        conversation_engine=MagicMock(),
    )


class _StubTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, status_code: int | None = None, raise_exc: Exception | None = None) -> None:
        self._status_code = status_code
        self._raise_exc = raise_exc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._status_code is not None
        return httpx.Response(self._status_code)


@pytest.fixture
def patch_httpx(monkeypatch: pytest.MonkeyPatch) -> dict[str, _StubTransport]:
    active: dict[str, _StubTransport] = {}
    real_init = httpx.AsyncClient.__init__

    def _init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        if "transport" in active:
            kwargs["transport"] = active["transport"]
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    return active


def test_health_live_returns_200_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness has no dependency I/O — always 200 unless the probe is marked dead."""
    monkeypatch.delenv("GPU_HOST", raising=False)
    client = TestClient(create_twilio_media_stream_app(_make_deps()))

    r = client.get("/health/live")

    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


def test_health_ready_returns_200_when_no_gpu_host_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without GPU_HOST there are no dependencies to probe — readiness == liveness."""
    monkeypatch.delenv("GPU_HOST", raising=False)
    client = TestClient(create_twilio_media_stream_app(_make_deps()))

    r = client.get("/health/ready")

    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


def test_health_ready_returns_200_when_all_gpu_services_healthy(
    monkeypatch: pytest.MonkeyPatch, patch_httpx: dict[str, _StubTransport]
) -> None:
    monkeypatch.setenv("GPU_HOST", "gpu.example.com")
    patch_httpx["transport"] = _StubTransport(status_code=200)
    client = TestClient(create_twilio_media_stream_app(_make_deps()))

    r = client.get("/health/ready")

    assert r.status_code == 200


def test_health_ready_returns_503_when_gpu_unreachable(
    monkeypatch: pytest.MonkeyPatch, patch_httpx: dict[str, _StubTransport]
) -> None:
    """503 tells k8s/LB to stop routing calls when the GPU node is down."""
    monkeypatch.setenv("GPU_HOST", "gpu.example.com")
    patch_httpx["transport"] = _StubTransport(raise_exc=httpx.ConnectError("no route"))
    client = TestClient(create_twilio_media_stream_app(_make_deps()))

    r = client.get("/health/ready")

    assert r.status_code == 503
    assert r.json() == {"status": "unhealthy"}


def test_legacy_health_route_still_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """/health (the pre-existing naive route) must keep returning 200 'ok' for
    backward compatibility with older monitors."""
    monkeypatch.delenv("GPU_HOST", raising=False)
    client = TestClient(create_twilio_media_stream_app(_make_deps()))

    r = client.get("/health")

    assert r.status_code == 200
    assert r.text == "ok"
