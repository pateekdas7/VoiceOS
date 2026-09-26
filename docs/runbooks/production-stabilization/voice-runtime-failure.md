# Voice runtime failure

```bash
systemctl status voiceos-voice-runtime
journalctl -u voiceos-voice-runtime -n 200 --no-pager
```
1. Determine whether the process, Redis/Postgres, or GPU dependency failed.
2. Follow the relevant dependency runbook before repeated restarts.
3. Restart once: `systemctl restart voiceos-voice-runtime`.
4. Verify runtime health/readiness and the WebSocket path before restoring traffic.
Do not clear StartLimit blindly. Runtime WebSocket recovery remains **RUNTIME EVIDENCE REQUIRED**.