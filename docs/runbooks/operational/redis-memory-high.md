# Runbook: RedisMemoryHigh
**Alert:** Redis used_memory >80% of maxmemory | **Severity:** warning (80%), critical (95%)

## What it means
Redis is approaching its memory limit. At 100% with noeviction policy, all writes fail — BFF sessions, dialer queues, and rate-limit counters break.

## Immediate steps
```bash
REDIS_PW=$(grep requirepass /etc/redis/redis.conf | awk '{print $2}')
redis-cli -a $REDIS_PW INFO memory | grep -E 'used_memory_human|maxmemory_human|mem_fragmentation'
```

## Identify large key spaces
```bash
# Top 10 key patterns by memory
redis-cli -a $REDIS_PW --bigkeys 2>/dev/null | tail -30

# Count keys per prefix
redis-cli -a $REDIS_PW --scan --pattern 'call:*' | wc -l
redis-cli -a $REDIS_PW --scan --pattern 'session:*' | wc -l
redis-cli -a $REDIS_PW --scan --pattern 'dialer:*' | wc -l
```

## Immediate relief
```bash
# Force expire stale session keys (adjust TTL as needed)
redis-cli -a $REDIS_PW --scan --pattern 'session:*' | xargs -I{} redis-cli -a $REDIS_PW EXPIRE {} 3600

# Trigger AOF rewrite to compact append log
redis-cli -a $REDIS_PW BGREWRITEAOF
```

## Escalate if
- >95% and cannot free memory without deleting active queues
- Evictions are happening on call-state keys (`evicted_keys` rising fast)

## Resolution
- Increase maxmemory in /etc/redis/redis.conf and `sudo systemctl restart redis-server`
- Set TTLs on keys that don't have them (session, rate-limit counters)
- Archive and flush stale completed-call records older than 7 days
