# Repeated service crashes
1. Check `systemctl status <voiceos-service>`.
2. Inspect `journalctl -u <voiceos-service> --since '-30 min'`.
3. Do not disable StartLimit or increase restart frequency to mask the cause.
4. Fix the first failure at the owning dependency/configuration.
5. Restart once and observe readiness before declaring recovery.
Environment: CPU node/systemd.