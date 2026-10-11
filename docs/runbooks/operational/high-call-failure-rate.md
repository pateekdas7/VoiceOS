# Runbook: HighCallFailureRate
**Alert:** Twilio call failure rate >10% | **Severity:** warning (10%), critical (25%)

## What it means
Calls are failing before connecting. Revenue impact is direct — each failed call is a missed collection opportunity.

## Immediate steps
```bash
# Check dialer worker is running
systemctl is-active voiceos-dialer-worker

# Look at recent call failures
journalctl -u voiceos-dialer-worker --since "30 minutes ago" --no-pager | grep -i "fail\|error\|twilio\|31\d\d\d" | tail -30
```

## Triage by failure type
```bash
# Twilio error codes in BFF logs
grep 'twilio\|31\d\d\|callStatus.*fail' /opt/voiceos/logs/bff.log | tail -30

# Is the GPU node reachable? (voice-runtime needs it for STT/LLM/TTS)
STT_URL=$(grep STT_BASE_URL /opt/voiceos/.env 2>/dev/null | cut -d= -f2)
[ -n "$STT_URL" ] && curl -sf "${STT_URL}/health/ready" && echo "GPU: OK" || echo "GPU: UNREACHABLE or URL not set"

# Twilio account status — check credentials work
node -e "
const twilio = require('twilio');
const c = new twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);
c.api.accounts(process.env.TWILIO_ACCOUNT_SID).fetch().then(a=>console.log('Twilio OK, status:',a.status)).catch(e=>console.error('Twilio ERROR:',e.message));
" --env-file /opt/voiceos/.env 2>/dev/null || echo "Check Twilio credentials in .env"
```

## Common failure codes
| Twilio Code | Meaning | Fix |
|-------------|---------|-----|
| 31005 | Connection error to TwiML | Check voice-runtime is running |
| 21210 | Invalid from number | Check TWILIO_FROM_NUMBER in .env |
| 13224 | Invalid phone number format | Check lead phone number normalization |
| 20003 | Authentication failure | Verify TWILIO_ACCOUNT_SID / AUTH_TOKEN |

## Escalate if
- Failure rate >25% for >10 minutes
- Twilio authentication failing (affects all calls)
- GPU node unreachable and voice-runtime cannot process calls

## Resolution
- GPU unreachable: ask GPU node owner to restart; calls will resume once STT/LLM/TTS are back
- Twilio auth: update credentials in /opt/voiceos/.env, restart voiceos-bff and voiceos-dialer-worker
- TwiML error: `sudo systemctl restart voiceos-voice-runtime`
