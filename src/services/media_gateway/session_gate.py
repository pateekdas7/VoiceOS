"""SessionGate — enforces auth-before-allocation and tracks admitted sessions.

The SessionGate is the security boundary between the authentication layer and
the media allocation layer.  No audio session is created until the gate has
verified that authentication succeeded.

Rules enforced (AR-2):
  1. Sessions are only admitted after AuthResult.success == True.
  2. Each CallId may hold at most one active session (duplicate prevention).
  3. Per-tenant session limits are enforced (limit configured per tenant;
     defaults to MAX_SESSIONS_PER_TENANT until Sprint-017 Policy Engine).
  4. Rejected connections increment the admission_rejections metric.

Architecture: V1 Ch3.5 (SessionGate); V6 Ch4 AR-2 (auth-before-allocation);
              V3 Ch17 (Observability — rejection metrics).
"""

from __future__ import annotations

import time

from src.services.media_gateway import metrics
from src.services.media_gateway.protocol import AdmittedSession

# Default per-tenant concurrent session cap until Sprint-017 Policy Engine.
_DEFAULT_MAX_SESSIONS_PER_TENANT: int = 100


class SessionGate:
    """Controls admission and release of call sessions in the Media Gateway.

    Thread-safety note: VoiceOS uses a single async event loop per process.
    All mutations happen on the event loop thread, so dict operations are
    effectively single-threaded.  If multi-threaded usage is ever required,
    add a threading.Lock (tracked as tech debt).

    Architecture: V1 Ch3.5; V6 Ch4 AR-2.
    """

    def __init__(
        self,
        *,
        max_sessions_per_tenant: int = _DEFAULT_MAX_SESSIONS_PER_TENANT,
    ) -> None:
        self._admitted: dict[str, AdmittedSession] = {}
        self._max_per_tenant = max_sessions_per_tenant

    # ------------------------------------------------------------------
    # Session admission
    # ------------------------------------------------------------------

    def admit(
        self,
        call_id: str,
        tenant_id: str,
        adapter_type: str,
        *,
        rtp_port: int = 0,
    ) -> AdmittedSession:
        """Admit a call session that has already passed authentication.

        Call this ONLY after AuthResult.success == True (AR-2).

        Args:
            call_id:      Unique call identifier (V1 Appendix A).
            tenant_id:    Owning tenant (AR-8).
            adapter_type: 'twilio' | 'sip_rtp'.
            rtp_port:     UDP port for SIP/RTP sessions (0 for Twilio).

        Returns:
            The newly created AdmittedSession.

        Raises:
            ValueError: If a session for this call_id already exists.
            PermissionError: If the tenant has reached its session cap.
        """
        if call_id in self._admitted:
            raise ValueError(f"Session already admitted for call_id={call_id!r}")

        tenant_count = sum(1 for s in self._admitted.values() if s.tenant_id == tenant_id)
        if tenant_count >= self._max_per_tenant:
            reason = "tenant_session_limit_exceeded"
            metrics.record_admission_rejection(reason=reason, adapter_type=adapter_type)
            raise PermissionError(f"Tenant {tenant_id!r} has reached the maximum of {self._max_per_tenant} sessions")

        session = AdmittedSession(
            call_id=call_id,
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            admitted_at=time.time(),
            rtp_port=rtp_port,
        )
        self._admitted[call_id] = session
        metrics.record_session_admitted(adapter_type=adapter_type)
        return session

    # ------------------------------------------------------------------
    # Session rejection
    # ------------------------------------------------------------------

    def reject(self, call_id: str, reason: str, adapter_type: str) -> None:
        """Record an authentication or policy rejection.

        Increments the admission_rejections Prometheus counter.  The call_id
        is logged for traceability but no session resource is allocated.

        Args:
            call_id:      The call that was rejected (for log correlation).
            reason:       Stable rejection reason code (maps to metric label).
            adapter_type: 'twilio' | 'sip_rtp'.
        """
        metrics.record_admission_rejection(reason=reason, adapter_type=adapter_type)

    # ------------------------------------------------------------------
    # Session release
    # ------------------------------------------------------------------

    def release(self, call_id: str) -> AdmittedSession | None:
        """Release an admitted session on disconnect.

        Decrements the active_sessions gauge.

        Args:
            call_id: The call session to release.

        Returns:
            The released AdmittedSession, or None if it was not admitted.
        """
        session = self._admitted.pop(call_id, None)
        if session is not None:
            metrics.record_session_released(adapter_type=session.adapter_type)
        return session

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_admitted(self, call_id: str) -> bool:
        """Return True when a session exists for this call_id."""
        return call_id in self._admitted

    def get_session(self, call_id: str) -> AdmittedSession | None:
        """Return the AdmittedSession for call_id, or None if not found."""
        return self._admitted.get(call_id)

    def session_count(self) -> int:
        """Total number of currently admitted sessions."""
        return len(self._admitted)

    def tenant_session_count(self, tenant_id: str) -> int:
        """Number of active sessions for the given tenant."""
        return sum(1 for s in self._admitted.values() if s.tenant_id == tenant_id)
