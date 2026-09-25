# Degraded dependency

Check `/health/live` and `/health/ready` separately. Identify the dependency from readiness, Prometheus target state, or alert annotations. Follow its runbook. Keep the service running when safe to degrade, but never force readiness to healthy. Restore the dependency and verify readiness returns to healthy.