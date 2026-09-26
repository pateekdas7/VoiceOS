# PostgreSQL failure

Use `infra/dr/runbooks/postgres-failover.md`.

```bash
systemctl status postgresql
journalctl -u postgresql -n 100 --no-pager
PGPASSWORD=<from-vault> psql -h localhost -U voiceos -d voiceos -c "SELECT 1;"
```
For corruption/data loss use the documented PITR procedure, not an ad-hoc data-directory replacement.