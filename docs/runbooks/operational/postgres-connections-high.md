# Runbook: PostgresConnectionsHigh
**Alert:** Active Postgres connections >80% of max_connections | **Severity:** warning

## What it means
Connection pool is near exhaustion. New requests will be rejected with "too many connections" — WebAPI and BFF fail to serve requests.

## Immediate steps
```bash
# Check current connection count vs limit
psql "$POSTGRES_DSN" -c "
SELECT count(*) used,
       (SELECT setting::int FROM pg_settings WHERE name='max_connections') max_conn,
       round(count(*)*100.0/(SELECT setting::int FROM pg_settings WHERE name='max_connections'),1) pct
FROM pg_stat_activity;"
```

## Identify connection hogs
```bash
# Long-running queries (>30s)
psql "$POSTGRES_DSN" -c "
SELECT pid, now()-query_start AS duration, state, left(query,80)
FROM pg_stat_activity
WHERE query_start IS NOT NULL AND now()-query_start > interval '30s'
ORDER BY duration DESC LIMIT 20;"

# Idle connections by application
psql "$POSTGRES_DSN" -c "
SELECT application_name, state, count(*)
FROM pg_stat_activity GROUP BY 1,2 ORDER BY 3 DESC;"
```

## Kill stuck connections
```bash
# Terminate idle connections older than 10 minutes
psql "$POSTGRES_DSN" -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state='idle' AND now()-state_change > interval '10 minutes';"
```

## Escalate if
- Killing idle connections doesn't reduce count below 70%
- Long-running queries appear from the same application repeatedly
- New connections are being refused (WebAPI crash-looping)

## Resolution
- Immediate: kill idle/long-running connections as above
- Medium-term: reduce `pool_size` in uvicorn/asyncpg config
- Permanent: add PgBouncer connection pooler in front of Postgres
