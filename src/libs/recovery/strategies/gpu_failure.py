"""GPUFailureStrategy — graceful TTS fade + re-queue on a surviving GPU.

On a GPU device failure, ConversationEngine must halt the *current* TTS
synthesis gracefully (avoid a hard audio cutoff) and the affected sessions
must be re-queued on a surviving GPU device rather than dropped. The
GPU-to-GPU failover mechanics already exist (``FailoverManager``,
Sprint-008) — this strategy is the reliability-layer orchestration around it:
halt playback first, then drain/re-admit via the injected failover port.

Architecture: V1 Ch7 (GPU Scheduler GPU-2 graceful failover); V3 Ch7.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.libs.contracts.primitives import CallId

from ..outcome import RecoveryOutcome


@runtime_checkable
class TTSHaltPort(Protocol):
    """Protocol for halting in-flight TTS synthesis gracefully (fade, not cut)."""

    async def halt_current_synthesis(self, call_id: CallId) -> None: ...


@runtime_checkable
class GPUFailoverPort(Protocol):
    """Protocol for the GPU Scheduler's failover mechanics (Sprint-008 FailoverManager)."""

    def handle_device_failure(self, failed_device_id: str) -> Any: ...


class GPUFailureStrategy:
    """Recovers active calls after a GPU device failure."""

    strategy_name = "gpu_failure"

    def __init__(self, gpu_failover: GPUFailoverPort, tts_halt: TTSHaltPort | None = None) -> None:
        self._gpu_failover = gpu_failover
        self._tts_halt = tts_halt

    async def recover(self, call_id: CallId, failed_device_id: str) -> RecoveryOutcome:
        """Halt current TTS for ``call_id``, then drain/re-queue ``failed_device_id``.

        Returns:
            A successful RecoveryOutcome carrying the failed device id and
            the failover mechanism's own result.
        """
        if self._tts_halt is not None:
            await self._tts_halt.halt_current_synthesis(call_id)
        failover_result = self._gpu_failover.handle_device_failure(failed_device_id)
        return RecoveryOutcome(
            success=True,
            detail={
                "call_id": call_id,
                "failed_device_id": failed_device_id,
                "failover_result": repr(failover_result),
            },
        )
