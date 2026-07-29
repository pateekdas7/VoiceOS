"""Prometheus metrics for the ConversationEngine (V3 Ch15; V7 Ch7).

Emits the standard RED metrics expected by SLO recording rules and burn-rate
alerts defined in deployment/prometheus/rules/slo_rules.yml:
  - voiceos_conversation_engine_request_duration_ms  (Histogram)
  - voiceos_conversation_engine_errors_total         (Counter)
  - voiceos_conversation_engine_requests_total       (Counter)
"""

from __future__ import annotations

from src.libs.observability.metrics import REDMetrics, get_red_metrics

# Process-wide singleton — imported by engine.py and any test that needs it.
red: REDMetrics = get_red_metrics("conversation_engine")
