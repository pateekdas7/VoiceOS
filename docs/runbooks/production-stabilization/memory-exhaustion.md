# Memory exhaustion
1. Inspect: `free -h`.
2. Identify consumers: `ps aux --sort=-%mem | head -20`.
3. Inspect service/container events using the existing deployment tooling.
4. If a service is repeatedly OOM-killed, investigate the first failure instead of disabling restart protection.
5. Verify health/readiness and memory headroom after remediation.
Environment: target host or Kubernetes cluster.