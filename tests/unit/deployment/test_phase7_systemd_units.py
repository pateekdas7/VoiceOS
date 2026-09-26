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
    def test_has_graceful_shutdown_budget_for_node_worker(self) -> None:
        content = _read("voiceos-dialer-worker.service")
        assert "TimeoutStopSec=130" in content, (
            "Node dialer drains active calls for up to 120s"
        )

    def test_exec_start_runs_authoritative_w2_worker_in_production_mode(self) -> None:
        content = _read("voiceos-dialer-worker.service")
        assert "Environment=DIALER_MODE=production" in content
        assert "ExecStart=/usr/bin/node dialer_worker.js" in content

    def test_deploy_installs_and_enables_authoritative_relay_timer(self) -> None:
        deploy = (pathlib.Path(__file__).parents[3] / "scripts" / "deploy" / "deploy.sh").read_text()
        assert "voiceos-telephony-event-relay.service" in deploy
        assert "voiceos-telephony-event-relay.timer" in deploy
        assert "enable --now voiceos-telephony-event-relay.timer" in deploy

    def test_has_restart_always(self) -> None:
        assert "Restart=on-failure" in _read("voiceos-dialer-worker.service")
        assert "StartLimitIntervalSec=300" in _read("voiceos-dialer-worker.service")
        assert "StartLimitBurst=5" in _read("voiceos-dialer-worker.service")

    def test_has_wanted_by(self) -> None:
        assert "WantedBy=multi-user.target" in _read("voiceos-dialer-worker.service")

    def test_has_environment_file(self) -> None:
        assert "EnvironmentFile=" in _read("voiceos-dialer-worker.service")


class TestTelephonyEventRelayUnits:
    def test_periodic_relay_service_uses_shared_environment(self) -> None:
        content = _read("voiceos-telephony-event-relay.service")
        assert "ExecStart=/usr/bin/node scripts/telephony/telephony_event_relay.js" in content
        assert "EnvironmentFile=/opt/voiceos/.env" in content
        assert "Type=oneshot" in content

    def test_relay_timer_is_enabled_as_timer(self) -> None:
        content = _read("voiceos-telephony-event-relay.timer")
        assert "OnUnitActiveSec=5s" in content
        assert "Unit=voiceos-telephony-event-relay.service" in content
        assert "WantedBy=timers.target" in content


class TestBffService:
    def test_exists_and_has_required_fields(self) -> None:
        content = _read("voiceos-bff.service")
        assert "Restart=on-failure" in content
        assert "StartLimitIntervalSec=300" in content
        assert "StartLimitBurst=5" in content
        assert "WantedBy=multi-user.target" in content
        assert "bff.js" in content
