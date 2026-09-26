# Recovery and rollback

Source of truth: `docs/runbooks/phase16-production-rollout-runbook.md`.

1. Record failing SHA and service state.
2. Use `bash scripts/deploy/rollback.sh --to <sha> --execute` only where its documented assumptions match the environment.
3. Verify migration state.
4. Restart services in documented order.
5. Verify live/ready, dependencies, queue state and critical metrics.
6. Preserve failed-version evidence until recovery is confirmed.
Never improvise a database downgrade.