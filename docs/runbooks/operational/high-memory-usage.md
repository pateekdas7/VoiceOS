# Runbook: HighMemoryUsage
**Alert:** Node memory used >90% | **Severity:** warning (85%), critical (92%)

## What it means
System is near OOM. The kernel OOM killer may start terminating processes — services can die unpredictably.

## Immediate steps
```bash
# Current memory state
free -h

# Top memory consumers
ps aux --sort=-%mem | head -15

# Check if OOM killer already fired
dmesg | grep -i "oom_kill" | tail -5
journalctl -k | grep -i "oom" | tail -10
```

## Identify the leak
```bash
# voiceos process memory over time (check logs for growth)
ps -o pid,rss,comm -p $(pgrep -f uvicorn) $(pgrep -f node) $(pgrep -f python3) 2>/dev/null

# Node.js BFF heap (if running)
curl -s http://localhost:8000/metrics 2>/dev/null | grep nodejs_heap
```

## Immediate relief
```bash
# Restart the highest-memory service (confirm which first)
sudo systemctl restart voiceos-voice-runtime  # usually the heaviest
# Wait 30s and re-check free -h
```

## Escalate if
- Memory still >90% after restarting the largest consumer
- OOM killer has already killed a service
- Swap usage >80%

## Resolution
- Identify growing service, restart it (Restart=always will handle it)
- Check for memory leaks in recent deployments — roll back if needed
- Add swap if not present: `sudo fallocate -l 4G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
