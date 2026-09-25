# PostgreSQL failure
1. Check: `systemctl status postgresql`.
2. Inspect: `journalctl -u postgresql -n 150 --no-pager`.
3. Probe: `psql -h 127.0.0.1 -U voiceos -d voiceos -c 'SELECT 1;'`.
4. Use `infra/dr/runbooks/postgres-failover.md` for failover and `scripts/backup/pg_restore_from_wal.sh` for authorized PITR.
5. Run `bash deployment/cpu/healthcheck.sh` before restoring traffic.
Environment: CPU/PostgreSQL host.