# Pager Setup — PagerDuty / Slack / JIRA

**Audience:** on-call lead or SRE provisioning the host for the first time.

## Overview

`monitoring/grafana/alerts/alertmanager.yml` references three external
receivers via `${…}` environment variables, which are rendered into the
live config by `envsubst` when the Alertmanager container starts.

- `PAGERDUTY_SERVICE_KEY` — routing key for the Events API v2
- `SLACK_WEBHOOK_URL`     — incoming webhook URL (`https://hooks.slack.com/services/...`)
- `JIRA_WEBHOOK_URL`      — any JSON-receiving endpoint (Automation rule, Zapier, your bridge service)
- `VOICEOS_ALERT_WEBHOOK_URL` — webapi webhook that persists to `alert_lifecycle` (defaults to `http://127.0.0.1:8000/webhooks/alertmanager` on single-host)

All four live in `/opt/voiceos/monitoring-stack/alertmanager-secrets.env`
on the host — this file is **not** committed and is loaded by compose via
`env_file:`.

## Fresh install

A fresh deploy writes placeholder values:

```
PAGERDUTY_SERVICE_KEY=REPLACE_ME_PAGERDUTY_SERVICE_KEY
SLACK_WEBHOOK_URL=REPLACE_ME_SLACK_WEBHOOK_URL
JIRA_WEBHOOK_URL=REPLACE_ME_JIRA_WEBHOOK_URL
VOICEOS_ALERT_WEBHOOK_URL=http://127.0.0.1:8000/webhooks/alertmanager
```

With placeholders in place, the webhook catch-all still works (every alert
is persisted to `alert_lifecycle`). PagerDuty / Slack / JIRA receivers
will log send failures to the alertmanager journal but will not block
other receivers.

## Cutover to real credentials

1. **Create the PagerDuty service:** PagerDuty → Services → New service → Integrations → Events API v2. Copy the *Integration Key*.
2. **Create the Slack incoming webhook:** `api.slack.com/apps` → Incoming Webhooks → Add New Webhook to Workspace → pick `#voiceos-alerts`. Copy the URL.
3. **Create the JIRA bridge:** either a JIRA Automation rule with "Incoming Webhook" trigger, or a Zapier/Make hook that calls `POST /rest/api/3/issue`. Copy the trigger URL.
4. **Edit the secrets file on the host:**
   ```bash
   sudo nano /opt/voiceos/monitoring-stack/alertmanager-secrets.env
   # Replace the three REPLACE_ME_* values with real keys/URLs. Save.
   sudo chmod 600 /opt/voiceos/monitoring-stack/alertmanager-secrets.env
   sudo chown root:root /opt/voiceos/monitoring-stack/alertmanager-secrets.env
   ```
5. **Restart the alertmanager container:**
   ```bash
   cd /opt/voiceos/monitoring-stack
   sudo docker compose up -d --force-recreate alertmanager
   ```
6. **Verify the entrypoint rendered the config:**
   ```bash
   sudo docker exec voiceos-alertmanager cat /etc/alertmanager/alertmanager.rendered.yml | grep -E 'routing_key|api_url|url:' | head -10
   ```
   None of the lines should contain `${` or `REPLACE_ME_`.
7. **Smoke test:** POST a synthetic critical alert and confirm it pages:
   ```bash
   curl -X POST -H 'Content-Type: application/json' \
     -d '[{"labels":{"alertname":"PagerDutySmokeTest","severity":"critical","service":"testify"},"annotations":{"summary":"smoke test — ignore"}}]' \
     http://localhost:9093/api/v2/alerts
   ```
   You should see:
   - PagerDuty incident created (acknowledge within 5 min and resolve)
   - Alert row in `alert_lifecycle` table

## Rotation

Rotate on the same cadence as other secrets in Vault (90-day default).
Re-run step 4 → step 5 → step 7 after any rotation.

## Troubleshooting

- **Alertmanager restarts in a loop** → `docker logs voiceos-alertmanager` will show `unsupported scheme "" for URL`. Means an env var is literally empty (not even a placeholder). Fix `alertmanager-secrets.env`, repeat step 5.
- **Alerts fire but PagerDuty doesn't incident** → check `docker logs voiceos-alertmanager --tail 50 | grep -i pagerduty`. Common cause: wrong routing key or an Events API v1 key pasted into a v2-expecting receiver.
- **Slack silent** → paste the webhook URL into `curl -X POST -H 'Content-Type: application/json' -d '{"text":"test"}' <URL>` manually; if that fails, the webhook was revoked.

## Related

- `monitoring/grafana/alerts/alertmanager.yml` — single source of truth
- `scripts/ci/alertmanager_config_drift.py` — CI gate
- `src/services/web_api/routes/webhooks.py` — alertmanager → `alert_lifecycle` ingest
