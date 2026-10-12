# Runbook: HighBFFErrorRate
**Alert:** BFF HTTP 5xx rate >5% for 5 minutes | **Severity:** warning/critical

## What it means
The Node.js BFF (port 8000) is returning server errors. Callers and the frontend cannot operate.

## Immediate steps
1. `systemctl status voiceos-bff.service` — confirm service is running
2. `tail -50 /opt/voiceos/logs/bff.log` — find the error class
3. `redis-cli -a $REDIS_PW ping` — rule out Redis unavailability
4. `curl -s http://localhost:8000/system/health` — check component health JSON
5. If OOM or crash-loop: `sudo systemctl restart voiceos-bff`

## Diagnostic commands
```bash
# Last 100 errors with timestamps
grep '"level":"ERROR"' /opt/voiceos/logs/bff.log | tail -100

# Redis connection check
redis-cli -a $(grep requirepass /etc/redis/redis.conf | awk '{print $2}') ping

# Postgres connection check
psql "$POSTGRES_DSN" -c "SELECT 1"
```

## Escalate if
- Error rate >20% for >10 minutes
- Redis or Postgres also down (multi-component failure)
- Restart does not clear the error rate within 5 minutes

## Resolution
- Single service crash: `sudo systemctl restart voiceos-bff`
- Upstream DB issue: follow redis-failover.md or postgres-failover.md
- Code bug: roll back last deployment via `sudo bash /opt/voiceos/app/scripts/rollback.sh`
