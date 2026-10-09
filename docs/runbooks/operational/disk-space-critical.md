# Runbook: DiskSpaceCritical
**Alert:** Disk usage >85% on any mount | **Severity:** warning (85%), critical (92%)

## What it means
Disk full kills PostgreSQL WAL archiving, log writing, and MongoDB journal writes — services start crashing.

## Immediate steps
```bash
# Which partition is full?
df -h | sort -k5 -rh | head -10

# Where is the space going?
du -sh /opt/voiceos/logs/* 2>/dev/null | sort -rh | head -10
du -sh /backup/pg_wal/ /backup/voiceos_pg/ 2>/dev/null | sort -rh
du -sh /var/log/ /tmp/ 2>/dev/null | sort -rh
```

## Quick wins — safe to delete
```bash
# Old pg_dump backups (keep last 3)
ls -t /backup/voiceos_pg/*.dump | tail -n +4 | xargs rm -f

# WAL segments older than 3 days
find /backup/pg_wal/ -mtime +3 -delete 2>/dev/null

# Old logs (keep last 7 days)
find /opt/voiceos/logs/ -name "*.log.*" -mtime +7 -delete 2>/dev/null

# Docker unused images/containers
docker system prune -f
```

## Escalate if
- /var/lib/postgresql or /var/lib/redis partition >90% — database writes will fail immediately
- Cannot free >5% within 10 minutes

## Resolution
- Immediate: run quick-win deletions above
- Adjust WAL cleanup cron: `crontab -e` (root), reduce retention from 7 days to 3 days
- Long-term: add external backup storage (S3/MinIO) and reduce local retention
