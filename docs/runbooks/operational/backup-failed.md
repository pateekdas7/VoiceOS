# Runbook: BackupJobFailed
**Alert:** Systemd backup timer service exited with non-zero status | **Severity:** warning

## Backup jobs monitored
| Timer | Service | Schedule |
|-------|---------|----------|
| voiceos-mongodb-backup.timer | voiceos-mongodb-backup.service | Daily ~18:30 |
| voiceos-postgres-backup.timer | voiceos-postgres-backup.service | Daily 02:00 |
| voiceos-vault-snapshot.timer | voiceos-vault-snapshot.service | Daily ~19:00 |
| voiceos-backup-verification.timer | voiceos-backup-verification.service | Daily 04:00 |

## Immediate steps
```bash
# Which job failed?
systemctl list-timers --all | grep voiceos

# Check the failed service
journalctl -u voiceos-<name>.service -n 50 --no-pager

# Check backup destination
ls -lh /backup/voiceos_pg/ 2>/dev/null
ls -lh /backup/voiceos_mongo/ 2>/dev/null
```

## Common causes and fixes
```bash
# 1. Disk full — check space first
df -h /backup/

# 2. Postgres backup failed — run manually to see error
sudo -u postgres pg_dump -U voiceos voiceos -F c -f /tmp/test.dump && echo OK

# 3. MongoDB backup failed — check container running
docker ps --filter name=voiceos-mongod

# 4. Re-run any backup manually
sudo systemctl start voiceos-postgres-backup.service
sudo systemctl start voiceos-mongodb-backup.service
```

## Escalate if
- Backup has been failing for >3 consecutive days (check `journalctl --since "3 days ago"`)
- Disk full and cannot be cleared — escalate to ops for storage expansion

## Resolution
Fix the root cause (disk/container/credentials), then manually start the service to confirm it passes before relying on the next scheduled run.
