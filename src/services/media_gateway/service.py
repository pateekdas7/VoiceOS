"""MediaGatewayService — lifecycle manager and adapter registry.

The MediaGatewayService is the entry point for all carrier transport integrations.
It maintains the registry of active TransportAdapters, enforces the
auth-before-allocation lifecycle (AR-2) via the SessionGate, and delegates
media streaming to the appropriate adapter.

Lifecycle:
    gw = MediaGatewayService()
    gw.start()
    # ... register adapters as calls arrive ...
    gw.stop()

Architecture: V1 Ch3 (Media Gateway service layer); V6 Ch4 AR-2.
"""

from __future__ import annotations

from src.services.media_gateway.protocol import (
    ADAPTER_TYPE_SIP_RTP,
    ADAPTER_TYPE_TWILIO,
    AuthResult,
    TransportAdapter,
)
from src.services.media_gateway.session_gate import SessionGate


class MediaGatewayService:
    """Manages transport adapters and enforces the auth-before-allocation rule.

    Responsibilities:
    - Maintain the SessionGate (tracks admitted sessions, rejects auth failures).
    - Register newly authenticated adapters with the session registry.
    - Provide session introspection for monitoring and health checks.

    The service does NOT own an event loop; it is driven by the
    asyncio event loop of the calling process.

    Architecture: V1 Ch3; V6 Ch4 AR-2.
    """

    ADAPTER_TYPE_TWILIO: str = ADAPTER_TYPE_TWILIO
    ADAPTER_TYPE_SIP_RTP: str = ADAPTER_TYPE_SIP_RTP

    def __init__(self) -> None:
        self._gate = SessionGate()
        self._adapters: dict[str, TransportAdapter] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the Media Gateway service (idempotent)."""
        self._running = True

    def stop(self) -> None:
        """Stop the Media Gateway service.

        Does not forcibly close existing sessions — callers should disconnect
        adapters before calling stop().
        """
        self._running = False

    # ------------------------------------------------------------------
    # Adapter admission (auth-before-allocation)
    # ------------------------------------------------------------------

    async def admit_adapter(
        self,
        call_id: str,
        tenant_id: str,
        adapter: TransportAdapter,
        credentials: dict[str, str],
        adapter_type: str,
        *,
        rtp_port: int = 0,
    ) -> AuthResult:
        """Authenticate and admit a transport adapter.

        Implements AR-2: authenticate() always runs before any session resource
        is allocated.  On failure the rejection is recorded in the SessionGate
        and the adapter is NOT registered.

        Args:
            call_id:      Unique call identifier.
            tenant_id:    Owning tenant (AR-8).
            adapter:      The TransportAdapter to authenticate and admit.
            credentials:  Carrier-specific credential dict.
            adapter_type: 'twilio' | 'sip_rtp'.
            rtp_port:     RTP port for SIP/RTP sessions (0 for Twilio).

        Returns:
            AuthResult — success=True means the adapter is admitted and connected.
        """
        result = await adapter.authenticate(credentials)
        if not result.success:
            self._gate.reject(
                call_id=call_id,
                reason=result.reason or "auth_failed",
                adapter_type=adapter_type,
            )
            return result

        # Admit the session in the gate.
        self._gate.admit(
            call_id=call_id,
            tenant_id=tenant_id,
            adapter_type=adapter_type,
            rtp_port=rtp_port,
        )

        # Connect the transport (now that auth succeeded — AR-2 satisfied).
        await adapter.connect()
        self._adapters[call_id] = adapter
        return result

    async def release_adapter(self, call_id: str) -> None:
        """Disconnect and release an admitted adapter.

        Args:
            call_id: The call session to release.
        """
        adapter = self._adapters.pop(call_id, None)
        if adapter is not None:
            await adapter.disconnect()
        self._gate.release(call_id)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_adapter(self, call_id: str) -> TransportAdapter | None:
        """Return the active TransportAdapter for call_id, or None."""
        return self._adapters.get(call_id)

    def active_session_count(self) -> int:
        """Total number of currently admitted sessions."""
        return self._gate.session_count()

    def is_session_admitted(self, call_id: str) -> bool:
        """Return True when a session is active for this call_id."""
        return self._gate.is_admitted(call_id)

    @property
    def gate(self) -> SessionGate:
        """The SessionGate instance (exposed for testing and monitoring)."""
        return self._gate

    @property
    def is_running(self) -> bool:
        """True between start() and stop()."""
        return self._running
