# Queue buildup

Use Prometheus queue-depth/age/DLQ alerts plus the existing Redis/dialer checks.
1. Determine whether workers are down, Redis is unhealthy, the calling window is closed, or a downstream provider is failing.
2. Fix the dependency first.
3. Do not blindly increase concurrency.
4. Verify queue depth decreases and duplicate call effects do not appear.