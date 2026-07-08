"""Prometheus metrics for the GPU Scheduler service.

Exposes the following metrics per V1 Ch7 and DocSuite-08 naming conventions:

    voiceos_gpu_vram_used_mb        Gauge   (label: device_id)
        VRAM currently allocated on each GPU device in MB.

    voiceos_gpu_vram_available_mb   Gauge   (label: device_id)
        VRAM currently available on each GPU device in MB.

    voiceos_gpu_admission_requests_total  Counter (label: service, decision)
        Total admission decisions issued (APPROVE or REJECT).

    voiceos_gpu_admission_rejections_total  Counter (label: service)
        Total admissions rejected due to insufficient VRAM (RI-8 guard).

Architecture: V1 Ch7 (GPU Scheduler observability); DocSuite-08.
"""

from __future__ import annotations

import prometheus_client as prom

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

VRAM_USED_MB: prom.Gauge = prom.Gauge(
    "voiceos_gpu_vram_used_mb",
    "VRAM currently allocated on each GPU device (MB)",
    ["device_id"],
)

VRAM_AVAILABLE_MB: prom.Gauge = prom.Gauge(
    "voiceos_gpu_vram_available_mb",
    "VRAM currently available on each GPU device (MB)",
    ["device_id"],
)

ADMISSION_REQUESTS_TOTAL: prom.Counter = prom.Counter(
    "voiceos_gpu_admission_requests_total",
    "Total VRAM admission decisions (APPROVE or REJECT)",
    ["service", "decision"],
)

ADMISSION_REJECTIONS_TOTAL: prom.Counter = prom.Counter(
    "voiceos_gpu_admission_rejections_total",
    "Total VRAM admission rejections due to insufficient VRAM",
    ["service"],
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def update_vram_gauges(device_id: str, used_mb: int, available_mb: int) -> None:
    """Update the VRAM used/available gauges for a device.

    Args:
        device_id:    GPU device identifier used as Prometheus label.
        used_mb:      Currently allocated VRAM in MB.
        available_mb: Currently available VRAM in MB.
    """
    VRAM_USED_MB.labels(device_id=device_id).set(used_mb)
    VRAM_AVAILABLE_MB.labels(device_id=device_id).set(available_mb)


def record_admission(service: str, approved: bool) -> None:
    """Record an admission decision for Prometheus.

    Args:
        service:  Requesting service name (label value).
        approved: True = APPROVE, False = REJECT.
    """
    decision = "APPROVE" if approved else "REJECT"
    ADMISSION_REQUESTS_TOTAL.labels(service=service, decision=decision).inc()
    if not approved:
        ADMISSION_REJECTIONS_TOTAL.labels(service=service).inc()
