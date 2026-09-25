# Memory exhaustion

```bash
free -h
systemctl --failed
journalctl -k -n 100 --no-pager
```
Identify the OOM source, preserve logs, then restart only the affected service. Recheck readiness/headroom and investigate restart storms before clearing systemd limits.