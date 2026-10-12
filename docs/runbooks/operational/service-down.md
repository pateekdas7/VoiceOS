# Runbook: VoiceOSServiceDown
**Alert:** Any VoiceOS systemd service not active | **Severity:** critical

## Services monitored
| Service | Port | Log |
|---------|------|-----|
| voiceos-bff | 8000 | /opt/voiceos/logs/bff.log |
| voiceos-webapi | 8001 | /opt/voiceos/logs/webapi.log |
| voiceos-dialer-worker | — | journalctl -u voiceos-dialer-worker |
| voiceos-voice-runtime | 8080 | /opt/voiceos/logs/voice-runtime.log |
| voiceos-frontend | 3000 | journalctl -u voiceos-frontend |

## Immediate steps
1. `for svc in bff webapi dialer-worker voice-runtime frontend; do systemctl is-active voiceos-$svc; done`
2. For any failed service: `systemctl status voiceos-<name> --no-pager -l`
3. Check its log for the crash reason
4. `sudo systemctl restart voiceos-<name>`
5. If still failing after restart, check `.env` for missing required variables

## Diagnostic commands
```bash
# Full status of all VoiceOS services
systemctl status voiceos-*.service --no-pager

# Last 30 lines of any service log
journalctl -u voiceos-<name>.service -n 30 --no-pager

# Check .env is intact
wc -l /opt/voiceos/.env  # should be ~70+ lines
```

## Escalate if
- Service fails to restart after 3 attempts
- Multiple services down simultaneously (likely infrastructure issue)
- Crash logs show OOM killer (`oom_kill_process`) — check `dmesg | tail -20`

## Resolution
- Single restart usually fixes transient crashes (systemd Restart=always handles it)
- Missing env var: add to /opt/voiceos/.env, then `sudo systemctl restart voiceos-<name>`
- OOM: check memory, consider reducing workers or restarting the node
