# Service down

Confirm whether the process/container is down or merely not ready. Inspect the first failure and use the service-specific W1 runbook. Restart only after dependency state is understood, then verify readiness and error-rate recovery. Commands differ between systemd and Kubernetes environments.