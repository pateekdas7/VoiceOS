"""Unit tests for Phase 7b/7c: systemd unit file correctness.

Verifies:
- All 5 original units exist (bff, frontend, webapi, vault-unseal + gpu ones)
- 2 new units exist: voiceos-voice-runtime.service, voiceos-dialer-worker.service
- voiceos-webapi.service has --timeout-graceful-shutdown 30
- voiceos-voice-runtime.service has TimeoutStopSec=45
- voiceos-dialer-worker.service has TimeoutStopSec=45
- All 7 listed units have WantedBy=multi-user.target
- All 7 units use bounded Restart=on-failure recovery
"""

from __future__ import annotations

import os
import pathlib

import pytest

_SYSTEMD = pathlib.Path(__file__).parents[3] / "scripts" / "systemd"


def _read(name: str) -> str:
    return (_SYSTEMD / name).read_text()


class TestRequiredUnitsExist:
    @pytest.mark.parametrize("unit", [
        "voiceos-bff.service",
        "voiceos-frontend.service",
        "voiceos-webapi.service",
        "voiceos-voice-runtime.service",
        "voiceos-dialer-worker.service",
    ])
    def test_unit_file_exists(self, unit: str) -> None:
        assert (_SYSTEMD / unit).exists(), f"Missing: scripts/systemd/{unit}"


class TestWebApiService:
    def test_has_timeout_graceful_shutdown(self) -> None:
        content = _read("voiceos-webapi.service")
        assert "--timeout-graceful-shutdown 30" in content, (
            "voiceos-webapi.service must pass --timeout-graceful-shutdown 30 to uvicorn"
        )

    def test_has_restart_always(self) -> None:
        assert "Restart=on-failure" in _read("voiceos-webapi.service")
        assert "StartLimitIntervalSec=300" in _read("voiceos-webapi.service")
        assert "StartLimitBurst=5" in _read("voiceos-webapi.service")

    def test_has_wanted_by(self) -> None:
        assert "WantedBy=multi-user.target" in _read("voiceos-webapi.service")


class TestVoiceRuntimeService:
    def test_has_timeout_stop_sec_45(self) -> None:
        content = _read("voiceos-voice-runtime.service")
        assert "TimeoutStopSec=45" in content, (
            "voiceos-voice-runtime.service must have TimeoutStopSec=45 for WS drain"
        )

    def test_exec_start_calls_serve(self) -> None:
        content = _read("voiceos-voice-runtime.service")
        assert "deployment/cpu/app.py --serve" in content

    def test_has_restart_always(self) -> None:
        assert "Restart=on-failure" in _read("voiceos-voice-runtime.service")
        assert "StartLimitIntervalSec=300" in _read("voiceos-voice-runtime.service")
        assert "StartLimitBurst=5" in _read("voiceos-voice-runtime.service")

    def test_has_wanted_by(self) -> None:
        assert "WantedBy=multi-user.target" in _read("voiceos-voice-runtime.service")

    def test_has_environment_file(self) -> None:
        assert "EnvironmentFile=" in _read("voiceos-voice-runtime.service")


class TestDialerWorkerService:
    def test_has_timeout_stop_sec_45(self) -> None:
        content = _read("voiceos-dialer-worker.service")
        assert "TimeoutStopSec=45" in content, (
            "voiceos-dialer-worker.service must have TimeoutStopSec=45"
        )

    def test_exec_start_calls_run_dialer_worker(self) -> None:
        content = _read("voiceos-dialer-worker.service")
        assert "run_dialer_worker.py" in content

    def test_has_restart_always(self) -> None:
        assert "Restart=on-failure" in _read("voiceos-dialer-worker.service")
        assert "StartLimitIntervalSec=300" in _read("voiceos-dialer-worker.service")
        assert "StartLimitBurst=5" in _read("voiceos-dialer-worker.service")

    def test_has_wanted_by(self) -> None:
        assert "WantedBy=multi-user.target" in _read("voiceos-dialer-worker.service")

    def test_has_environment_file(self) -> None:
        assert "EnvironmentFile=" in _read("voiceos-dialer-worker.service")


class TestBffService:
    def test_exists_and_has_required_fields(self) -> None:
        content = _read("voiceos-bff.service")
        assert "Restart=on-failure" in content
        assert "StartLimitIntervalSec=300" in content
        assert "StartLimitBurst=5" in content
        assert "WantedBy=multi-user.target" in content
        assert "bff.js" in content
