# Runbook: HighWebAPIErrorRate
**Alert:** WebAPI HTTP 5xx rate >5% for 5 minutes | **Severity:** warning/critical

## What it means
The Python/uvicorn WebAPI (port 8001) is returning server errors. Tenant management, billing, and campaign APIs are broken.

## Immediate steps
1. `systemctl status voiceos-webapi.service`
2. `tail -50 /opt/voiceos/logs/webapi.log`
3. Check Postgres connectivity: `psql "$POSTGRES_DSN" -c "SELECT 1"`
4. Check MongoDB: `docker exec voiceos-mongod mongosh --eval "db.adminCommand('ping')"`
5. Restart if crash-loop: `sudo systemctl restart voiceos-webapi`

## Diagnostic commands
```bash
# Python tracebacks
grep "Traceback\|ERROR\|Exception" /opt/voiceos/logs/webapi.log | tail -50

# Active DB connections
psql "$POSTGRES_DSN" -c "SELECT count(*) FROM pg_stat_activity WHERE state='active';"

# Vault token validity
VAULT_TOKEN=$(grep ^VAULT_TOKEN /opt/voiceos/.env | cut -d= -f2)
VAULT_ADDR=http://127.0.0.1:8200 vault token lookup $VAULT_TOKEN
```

## Escalate if
- Error rate >20% for >10 minutes
- Postgres or MongoDB also down
- Vault token expired (renew with app_token.json)

## Resolution
- Restart service: `sudo systemctl restart voiceos-webapi`
- Vault token expired: re-run `/opt/voiceos/app/scripts/vault/bootstrap_vault.sh` Step 10
- DB migration needed: `cd /opt/voiceos/app && alembic upgrade head`
