# Queue buildup
1. Inspect the existing `voiceos_queue_depth` metric.
2. Run `bash deployment/cpu/healthcheck.sh` and inspect EventBus/DLQ output.
3. Check `systemctl status voiceos-dialer-worker` and recent worker logs.
4. Identify downstream failure before increasing concurrency.
5. Resume processing only after dependency health and queue convergence are observed.
Environment: CPU node plus Prometheus/Redis.