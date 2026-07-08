"""SupervisorService — monitor / barge-in / override with live context (V5 Ch7.7; V4 Ch15).

Architecture: V5 Ch7 (Contact Center Platform — Supervisor); V4 Ch15 (Human Oversight).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.libs.audit.logger import AuditLogger
from src.libs.contracts.primitives import TenantId

from .live_transfer import AudioBridgePort


@dataclass(frozen=True)
class MonitorSession:
    """A read-only supervisor monitoring session (V5 Ch7.7 ``monitor`` — does not interrupt the call)."""

    call_id: str
    supervisor_id: str
    started_at: datetime


class SupervisorService:
    """Supervisor monitor/barge-in/override — every action is audited (V4 Ch15 §15.17)."""

    def __init__(self, audio_bridge: AudioBridgePort, audit_logger: AuditLogger | None = None) -> None:
        self._audio_bridge = audio_bridge
        self._audit_logger = audit_logger

    def monitor(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> MonitorSession:
        """Read-only: live audio + transcript + AI decision stream. Never touches the audio bridge."""
        self._audit("MONITOR", tenant_id, call_id, supervisor_id)
        return MonitorSession(call_id=call_id, supervisor_id=supervisor_id, started_at=datetime.now(UTC))

    def barge_in(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        """Mix supervisor audio in; all parties hear the supervisor; AI is muted."""
        self._audio_bridge.mute_ai(call_id)
        self._audit("BARGE_IN", tenant_id, call_id, supervisor_id)

    def override(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        """Supervisor takes full control; the AI conversation engine is paused (muted)."""
        self._audio_bridge.mute_ai(call_id)
        self._audit("OVERRIDE", tenant_id, call_id, supervisor_id)

    def release(self, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        """End a barge-in/override and hand the call back to the AI."""
        self._audio_bridge.unmute_ai(call_id)
        self._audit("RELEASE", tenant_id, call_id, supervisor_id)

    def is_ai_muted(self, call_id: str) -> bool:
        return self._audio_bridge.is_ai_muted(call_id)

    def _audit(self, action: str, tenant_id: TenantId, call_id: str, supervisor_id: str) -> None:
        if self._audit_logger is None:
            return
        self._audit_logger.record(
            tenant_id,
            supervisor_id,
            f"supervisor.{action.lower()}",
            "Call",
            call_id,
            "SUCCESS",
        )
