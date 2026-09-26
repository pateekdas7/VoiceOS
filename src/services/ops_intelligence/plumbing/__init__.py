"""ops_intelligence.plumbing -- the always-on, non-AI sub-component (ADR-006 Sec 3.0).

No module in this package may import anything from ``..reasoning`` or any
LLM/reasoning-model adapter (enforced by ``scripts/check_boundaries.py``,
Rule 6). Every alert lifecycle transition here has zero dependency on the
reasoning model and must keep working exactly as today if ``reasoning/`` is
completely disabled (ADR-006 Sec 13.12).
"""

from __future__ import annotations

from .alert_lifecycle import AlertLifecycleService, AlertNotificationPort, NullAlertNotificationPort, make_fingerprint
from .registration import COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE, register_ops_intelligence_consumers
from .repository import AlertRepositoryPort
from .webhook_app import create_webhook_app
from .webhook_ingest import WebhookIngestService

__all__ = [
    "COMPLIANCE_VIOLATION_ALERT_EVENT_TYPE",
    "AlertLifecycleService",
    "AlertNotificationPort",
    "AlertRepositoryPort",
    "NullAlertNotificationPort",
    "WebhookIngestService",
    "create_webhook_app",
    "make_fingerprint",
    "register_ops_intelligence_consumers",
]
