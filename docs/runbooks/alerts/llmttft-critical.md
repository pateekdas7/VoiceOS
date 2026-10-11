# Runbook: LLMTTFTCritical

**Alert source:** `monitoring/prometheus/alert_rules/*.yml` — alert `LLMTTFTCritical`.

## Impact

Fill in the user-visible impact of this alert firing (e.g. "collection
calls fail to connect", "campaign metrics stale", "DPDP notification
window missed"). The impact drives whether this is a page or a ticket.

## Immediate triage (first 5 minutes)

1. Open the relevant Grafana dashboard — see
   `monitoring/grafana/dashboards/` for the full list.
2. Check `/healthz` on each affected service — a hard failure here means
   the underlying process is unhealthy, not just the metric.
3. Correlate with Loki: filter by `service=<service>` and
   `app_level=~"warn|error|fatal"` over the alert window.
4. If this is a `severity: critical` alert, start an incident in the
   admin portal (`/admin/incidents`) and attach the alert fingerprint.

## Known fixes

- *Fill in as the team learns.* The point of the runbook is to be the
  shortest path from pager → resolution. Document causes you've already
  seen in production, not hypotheticals.

## Related

- SLO: *link the SLO this alert protects, if any.*
- Dashboard: `monitoring/grafana/dashboards/<relevant>.json`
- Dependent services: *list from `MONITORING_DEPENDENCY_MAP.md`*.
