# Recovery and rollback
1. Identify the deployed commit before changing anything.
2. Use `scripts/deploy/` and `docs/runbooks/phase16-production-rollout-runbook.md`.
3. Use the repository's established rollback mechanism; never perform a blind database rollback.
4. Verify schema compatibility before changing application versions.
5. Run service liveness/readiness, `bash deployment/cpu/healthcheck.sh`, and applicable smoke/regression tests.
6. Record exact commands, timestamps, observed state and commit SHA in `LEVEL2_RUNTIME_EVIDENCE.md`.
Environment: authorized staging/production host.