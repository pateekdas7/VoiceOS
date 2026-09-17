"""IncidentClassifier — maps alert labels to severity and affected services."""
from __future__ import annotations

from .models import IngestAlert, IncidentSeverity

# alert_name prefix -> service name
_ALERT_SERVICE_MAP: dict[str, str] = {
    "STT": "stt",
    "LLM": "llm",
    "TTS": "tts",
    "ConversationEngine": "conversation_engine",
    "Postgres": "postgres",
    "Redis": "redis",
    "NodeMemory": "node",
    "NodeDisk": "node",
    "NodeCPU": "node",
    "SLO": "platform",
    "APIError": "api_platform",
}


class IncidentClassifier:
    def classify_severity(self, alert: IngestAlert) -> IncidentSeverity:
        """Map alert severity string to IncidentSeverity."""
        if alert.severity in ("critical", "CRITICAL"):
            return IncidentSeverity.CRITICAL
        return IncidentSeverity.WARNING

    def classify_services(self, alerts: list[IngestAlert]) -> list[str]:
        """Extract unique affected services from a group of alerts."""
        services: set[str] = set()
        for alert in alerts:
            # check label first
            svc = alert.labels.get("service") or alert.labels.get("job")
            if svc:
                services.add(svc)
                continue
            # fall back to alert_name prefix matching
            for prefix, mapped in _ALERT_SERVICE_MAP.items():
                if alert.alert_name.startswith(prefix):
                    services.add(mapped)
                    break
        return sorted(services)

    def build_title(self, alerts: list[IngestAlert], severity: IncidentSeverity) -> str:
        """Compose a human-readable incident title."""
        if not alerts:
            return f"{severity} incident"
        names = sorted({a.alert_name for a in alerts})
        if len(names) == 1:
            return f"{severity}: {names[0]}"
        return f"{severity}: {names[0]} + {len(names) - 1} more"


__all__ = ["IncidentClassifier"]
