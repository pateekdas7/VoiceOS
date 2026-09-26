# Web API failure
1. Check: `systemctl status voiceos-webapi`.
2. Inspect: `journalctl -u voiceos-webapi -n 100 --no-pager`.
3. Probe: `curl -i http://127.0.0.1:8001/health/live` and `curl -i http://127.0.0.1:8001/health/ready`.
4. Recover after identifying the failure: `systemctl restart voiceos-webapi`.
5. Verify readiness before restoring traffic.
Environment: CPU node.