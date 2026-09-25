# Voice runtime failure
1. Check: `systemctl status voiceos-voice-runtime`.
2. Inspect: `journalctl -u voiceos-voice-runtime -n 150 --no-pager`.
3. Probe: `curl -i http://127.0.0.1:8010/health/live` and `curl -i http://127.0.0.1:8010/health/ready`.
4. Recover: `systemctl restart voiceos-voice-runtime`.
5. Confirm GPU dependencies are ready before accepting calls.
Environment: CPU node plus reachable GPU services.