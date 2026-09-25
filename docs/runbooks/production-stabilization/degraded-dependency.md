# Degraded dependency
1. Distinguish `/health/live` from `/health/ready`.
2. Inspect the dependency-specific alert and service logs.
3. Keep an unready service out of new traffic; do not restart a healthy process merely because a dependency is unavailable.
4. Use existing bounded retries/circuit breakers.
5. Restore traffic only after dependency health and backlog convergence are verified.
Environment: target service and dependency runtime.