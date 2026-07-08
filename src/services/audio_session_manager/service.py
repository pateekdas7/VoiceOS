"""AudioSessionManagerService — manages per-call AudioSession instances.

The service acts as the registry and lifecycle manager for all active audio
sessions.  It receives authenticated call_ids from the Media Gateway (after
AR-2 auth-before-allocation has been satisfied) and creates, looks up, and
releases AudioSession objects.

Lifecycle:
    svc = AudioSessionManagerService()
    svc.start()
    session = svc.create_session(call_id, tenant_id)
    # ... push frames via session.push_frame() ...
    svc.release_session(call_id)
    svc.stop()

Architecture: V1 Ch4 (Audio Session Manager — service layer);
              DocSuite-02 (AudioSessionManager ↔ AudioPreprocessor interface).
"""

from __future__ import annotations

from src.services.audio_session_manager.metrics import set_active_sessions
from src.services.audio_session_manager.session import AudioSession, SessionState


class AudioSessionManagerService:
    """Registry and lifecycle manager for per-call AudioSession objects.

    Thread-safety: the service is single-threaded and asyncio-compatible.
    All frame processing happens synchronously on the caller's thread/task.

    Architecture: V1 Ch4 (Audio Session Manager service).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AudioSession] = {}
        self._running: bool = False

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mark the service as running (idempotent)."""
        self._running = True

    def stop(self) -> None:
        """Mark the service as stopped.

        Does not forcibly close existing sessions; callers should call
        ``release_session()`` for each active session before stopping.
        """
        self._running = False

    @property
    def is_running(self) -> bool:
        """True between ``start()`` and ``stop()``."""
        return self._running

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(self, call_id: str, tenant_id: str) -> AudioSession:
        """Create and register a new AudioSession in CONNECTING state.

        Args:
            call_id:   Unique call identifier (must match Media Gateway).
            tenant_id: Owning tenant (AR-8).

        Returns:
            New AudioSession in CONNECTING state.

        Raises:
            ValueError: If a session with ``call_id`` already exists.
        """
        if call_id in self._sessions:
            raise ValueError(f"Session {call_id!r} already exists — release it first")
        session = AudioSession(call_id=call_id, tenant_id=tenant_id)
        self._sessions[call_id] = session
        set_active_sessions(self.active_session_count)
        return session

    def get_session(self, call_id: str) -> AudioSession | None:
        """Return the AudioSession for ``call_id``, or None if not found.

        Args:
            call_id: Call identifier to look up.
        """
        return self._sessions.get(call_id)

    def release_session(self, call_id: str) -> None:
        """Orderly shutdown and removal of a session.

        Transitions the session to ENDING → CLOSED and removes it from the
        registry.  No-op if the session does not exist.

        Args:
            call_id: Session to release.
        """
        session = self._sessions.pop(call_id, None)
        if session is None:
            return
        if session.state not in (SessionState.ENDING, SessionState.CLOSED):
            session.begin_ending()
        if session.state == SessionState.ENDING:
            session.close()
        set_active_sessions(self.active_session_count)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def active_session_count(self) -> int:
        """Number of sessions in ACTIVE or BARGE_IN state."""
        return sum(1 for s in self._sessions.values() if s.state not in (SessionState.ENDING, SessionState.CLOSED))

    @property
    def total_session_count(self) -> int:
        """Total number of tracked sessions (all states)."""
        return len(self._sessions)
