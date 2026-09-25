# MongoDB failure
1. Check: `systemctl status mongod`.
2. Inspect: `journalctl -u mongod -n 150 --no-pager`.
3. Probe with the configured MongoDB URI: `mongosh "$MONGODB_URL" --quiet --eval 'db.adminCommand("ping").ok'`.
4. Use `infra/dr/runbooks/mongo-restore.md` and `scripts/backup/mongodb_restore.sh` for authorized recovery.
5. Verify backup artifacts after recovery.
Environment: CPU/MongoDB host.