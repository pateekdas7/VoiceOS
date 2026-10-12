# Runbook: VoiceOSStartupHandlersBelowBaseline

**Alert source:** `monitoring/prometheus/alert_rules/application.yml` — alert
`VoiceOSStartupHandlersBelowBaseline`.

## Impact

Monitoring freshness degrades silently. The webapi registers 3 background
handlers at startup via `BackgroundHandlerRegistry`:

1. GPU fleet poller — refreshes `voiceos_gpu_fleet_*` metrics
2. Escalation evaluator — advances `alert_lifecycle` state machine
3. Alert_lifecycle poller — exports `voiceos_alert_lifecycle_scheduler_*`

If any of these fails to register, Prometheus continues scraping the webapi
but the affected metric family goes stale. Dashboards keep rendering old
values; alerts that depend on them stop firing accurately.

## Immediate triage (first 5 minutes)

1. Open the **Monitoring Self-Health** Grafana dashboard — the
   `voiceos_startup_handlers` panel will show the drop and the timestamp.
2. On the host, read the webapi journal for the last 10 minutes:
   `journalctl -u voiceos-webapi -n 200 --since "10 min ago" | grep -iE
   "BackgroundHandlerRegistry|handler|startup"`
3. Confirm which handler is missing by curling the webapi metrics:
   `curl -s http://localhost:8000/metrics | grep voiceos_startup_handlers`
4. Cross-check against `voiceos_gpu_fleet_poller_last_run_timestamp`,
   `voiceos_alert_lifecycle_scheduler_last_run_timestamp`,
   `voiceos_escalation_scheduler_last_run_timestamp` — the one with a
   stale `last_run` is the handler that failed.

## Known fixes

- A handler that raised an exception during `start()` is logged by
  `BackgroundHandlerRegistry` with the task name — restart webapi
  (`systemctl restart voiceos-webapi.service`) after fixing the root cause.
- If `POSTGRES_DSN` is missing or wrong, the escalation evaluator skips
  registration gracefully and the gauge legitimately reads 2, not 3.
  Fix the env var in `/opt/voiceos/app/.env` and restart webapi.
- A Vault / secret-fetch timeout on startup can defer registration past
  the gauge-emit point. Check `vault status` and the webapi's secret
  cache logs.

## Related

- Dashboard: `monitoring/grafana/dashboards/monitoring_self_health.json`
- Dependent services: webapi, scheduler
- Registry source: `src/libs/observability/metrics.py` (`BackgroundHandlerRegistry`)
