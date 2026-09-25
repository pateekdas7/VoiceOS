# MongoDB failure

```bash
systemctl status mongod
journalctl -u mongod -n 100 --no-pager
mongosh --quiet --eval "db.adminCommand('ping').ok"
```
For process failure, restart and verify replica-set/index state. For data loss use `infra/dr/runbooks/mongo-restore.md`; live restore verification is **RUNTIME EVIDENCE REQUIRED**.