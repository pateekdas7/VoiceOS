# Redis failure

Use `infra/dr/runbooks/redis-failover.md` as the source of truth.

```bash
redis-cli -a "$REDIS_PASSWORD" ping
systemctl status redis-server
journalctl -u redis-server -n 100 --no-pager
systemctl restart redis-server
redis-cli PING
redis-cli INFO persistence | grep aof_last_write_status
python3 scripts/eventbus_recovery.py
bash infra/dr/scripts/verify-recovery.sh --component redis
```
Do not promote a replica unless the current topology requires it.