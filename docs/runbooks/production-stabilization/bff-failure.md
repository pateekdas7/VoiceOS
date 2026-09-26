# BFF failure
1. Check: `systemctl status voiceos-bff`.
2. Inspect: `journalctl -u voiceos-bff -n 100 --no-pager`.
3. Probe: `curl -i http://127.0.0.1:8000/health/live` and `curl -i http://127.0.0.1:8000/health/ready`.
4. Recover with the existing unit: `systemctl restart voiceos-bff`.
5. Recheck readiness and logs. If StartLimit is reached, investigate the first failure before clearing it.
Environment: CPU node/systemd.