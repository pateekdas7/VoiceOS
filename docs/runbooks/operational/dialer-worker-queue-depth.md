# Runbook: DialerQueueDepthHigh
**Alert:** Dialer Redis queue depth >1000 leads | **Severity:** warning

## What it means
Leads are piling up faster than the dialer can process them. Calls are delayed; campaign SLAs may be missed.

## Immediate steps
```bash
REDIS_PW=$(grep requirepass /etc/redis/redis.conf | awk '{print $2}')

# Check queue depth per pipeline
redis-cli -a $REDIS_PW --scan --pattern 'dialer:queue:*' | \
  xargs -I{} sh -c 'echo "$(redis-cli -a '$REDIS_PW' LLEN {}) {}"' | sort -rn | head -10

# Check dialer worker status
systemctl status voiceos-dialer-worker --no-pager
journalctl -u voiceos-dialer-worker -n 50 --no-pager
```

## Check for blocked worker
```bash
# Is the worker processing? (log should show recent INITIATED entries)
journalctl -u voiceos-dialer-worker --since "5 minutes ago" --no-pager | grep -i "initiated\|error\|fatal"

# Twilio rate limit check
journalctl -u voiceos-dialer-worker --since "5 minutes ago" --no-pager | grep -i "429\|rate.limit"
```

## Resolution
- Worker crashed: `sudo systemctl restart voiceos-dialer-worker`
- Twilio rate-limited: queue will drain automatically once rate resets (check Twilio console)
- Legitimate volume spike: normal — queue will drain. Alert is informational at warning level.
- Persistent backlog: check if dialer worker is in a retry loop on failed calls (check `dialer:retry:*` keys)
