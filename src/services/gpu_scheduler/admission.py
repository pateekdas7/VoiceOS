"""AdmissionController — APPROVE/REJECT decisions for VRAM requests.

The controller never queues a request to OOM: when insufficient VRAM is
available the answer is always REJECT (RI-8 OOM-by-construction).

Selection policy: prefer the healthy GPU with the most available VRAM
(best-fit), so hot-path requests land on the device with the most headroom.

Architecture: V1 Ch7 (Admission Control); V1 Appendix E RI-8.
"""

from __future__ import annotations

from enum import StrEnum

from .vram_ledger import VRAMLedger


class AdmissionDecision(StrEnum):
    """Result of an AdmissionController.decide() call."""

    APPROVE = "APPROVE"
    """VRAM is available; caller may proceed with allocation."""
    REJECT = "REJECT"
    """Insufficient VRAM; request refused (not queued to OOM, per RI-8)."""


class AdmissionController:
    """Stateless admission gate backed by the VRAMLedger.

    decide() returns the decision AND the target device_id (None on REJECT),
    so the caller can immediately allocate on the selected device without a
    second round-trip to the ledger.

    Architecture: V1 Ch7 (Admission); V7 Ch6 (GPU fleet management).
    """

    def __init__(self, ledger: VRAMLedger) -> None:
        self._ledger = ledger

    def decide(
        self,
        service: str,
        model: str,
        required_vram_mb: int,
    ) -> tuple[AdmissionDecision, str | None]:
        """Decide whether to approve or reject a VRAM request.

        Args:
            service:          Requesting service name (e.g. 'stt', 'tts').
            model:            Model identifier (e.g. 'whisper-large-v3-turbo').
            required_vram_mb: VRAM needed in megabytes.

        Returns:
            ``(AdmissionDecision.APPROVE, device_id)`` when VRAM is available.
            ``(AdmissionDecision.REJECT, None)`` when no device has enough VRAM.
        """
        device_id = self._ledger.best_fit_device(required_vram_mb)
        if device_id is None:
            return AdmissionDecision.REJECT, None
        return AdmissionDecision.APPROVE, device_id
