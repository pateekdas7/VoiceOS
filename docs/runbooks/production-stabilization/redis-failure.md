# Redis failure
1. Check: `systemctl status redis-server` and `redis-cli ping`.
2. Inspect: `journalctl -u redis-server -n 100 --no-pager`.
3. Verify persistence: `bash scripts/backup/redis_verify_persistence.sh`.
4. Use `infra/dr/runbooks/redis-failover.md` for failover; do not invent a second recovery mechanism.
5. After recovery, verify EventBus and queue state with `bash deployment/cpu/healthcheck.sh`.
Environment: CPU node.