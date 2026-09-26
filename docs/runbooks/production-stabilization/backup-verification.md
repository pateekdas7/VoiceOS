# Backup verification

```bash
sudo systemctl status voiceos-backup-verification.timer
sudo systemctl start voiceos-backup-verification.service
journalctl -u voiceos-backup-verification.service -n 200 --no-pager
```
A successful artifact check does not prove restore correctness. A non-destructive restore drill is still required for production verification.