# DR Runbook — MongoDB Restore Drill

Companion to `infra/dr/scripts/trigger-mongo-restore.sh` (A9).

## Scope

Proves that VoiceOS's MongoDB `voiceos` database can be dumped and restored end-to-end, and that the resulting dataset round-trips without collection loss. This is the Mongo-side counterpart to the Postgres PITR drill (`trigger-db-failover.sh`, Sprint-027) that produced `DR_DRILL_REPORT_2026-07-08.md`.

**Non-destructive.** The live `voiceos` DB is never touched. The dump is restored into a scratch DB `voiceos_dr_drill` on the same instance.

## Preconditions

1. MongoDB is running with `--auth` enforced (see `scripts/vault/provision_datastore_auth.sh`).
2. `MONGO_PASSWORD` is available in the environment — export it from Vault:
   ```
   source <(python3 scripts/vault/gen_env.py)
   ```
3. `mongodump`, `mongorestore`, `mongosh` are installed (Ubuntu: `mongodb-org-tools` package, MongoDB 7.x repo).
4. Backup destination writable: `/opt/voiceos/backups/mongodb/` (or override with `MONGO_BACKUP_DIR`).

## Procedure

```bash
sudo -E bash infra/dr/scripts/trigger-mongo-restore.sh
```

Elapsed seconds are written to `/tmp/voiceos_dr_mongo_elapsed_seconds`.

## Success criteria

- `mongodump` exits 0 and produces a non-empty directory under `${MONGO_BACKUP_DIR}/drill-<timestamp>/voiceos/`.
- `mongorestore` exits 0.
- Collection count in `voiceos` equals collection count in `voiceos_dr_drill` (script asserts this — non-zero exit on mismatch).
- Target elapsed time: **≤ 300s** for the pilot dataset (5 collections, low-thousands of documents). Adjust upward as data grows.

## Cleanup

The scratch `voiceos_dr_drill` DB is left in place for manual spot-checks (e.g. `db.call_transcripts.countDocuments()` sanity check). To drop:

```bash
mongosh --host 127.0.0.1 --port 27017 \
  --username voiceos --password "${MONGO_PASSWORD}" \
  --authenticationDatabase voiceos \
  --eval "db.getSiblingDB('voiceos_dr_drill').dropDatabase()"
```

The dump directory (`${MONGO_BACKUP_DIR}/drill-*/`) is left in place — retention is caller-managed. For real backups (not drills), move the dump off-box to S3 or equivalent.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `MONGO_PASSWORD must be set` | Forgot to source gen_env.py | `source <(python3 scripts/vault/gen_env.py)` |
| `Authentication failed` | Vault-stored password out of sync with mongo user | Compare `vault kv get -field=value secret/voiceos/mongodb` vs. what was set via `db.getSiblingDB('voiceos').createUser(...)` |
| `mongodump produced no output` | The `voiceos` DB doesn't exist yet (fresh node) | Run a call end-to-end first to create collections; drill isn't meaningful on empty state |
| Collection count mismatch | Restore was interrupted or `--nsFrom/--nsTo` misapplied | Re-run — script is idempotent (drops scratch DB first) |

## What this drill does NOT prove

- **Point-in-time recovery.** MongoDB Community's oplog isn't a full WAL. A proper PITR drill would require a replica-set with a continuous oplog-tailing backup agent (Percona PBM, Ops Manager). Out of scope for the single-node pilot; filed as **TT-024** for post-pilot.
- **Cross-region durability.** Backups must be shipped off-box (S3 with SSE-KMS, per V3 DR spec) — this drill runs on the same host and does not exercise the transfer path.

## Reporting

Append the elapsed time and success/failure to the next `infra/dr/DR_DRILL_REPORT_<date>.md` (template at `DR_DRILL_REPORT_TEMPLATE.md`). Include: dump size, collection count, restore time, any deviations from success criteria.
