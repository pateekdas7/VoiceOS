# Dialer failure

```bash
systemctl status voiceos-dialer-worker
journalctl -u voiceos-dialer-worker -n 200 --no-pager
```
1. Confirm Redis is ready.
2. Restart once: `systemctl restart voiceos-dialer-worker`.
3. Verify queue consumption and terminal callback progression.
4. If callbacks fail, follow the existing callback-auth/stuck-call alerts.
Do not increase concurrency during an outage without evidence.