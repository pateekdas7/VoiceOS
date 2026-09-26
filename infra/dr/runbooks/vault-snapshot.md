# DR Runbook — Vault Snapshot & Restore

Companion to `infra/dr/scripts/backup-vault.sh` (A9).

## Scope

Snapshots and restores the single-node Vault deployment (file storage backend, `/opt/vault/data`). This is the Vault-side counterpart to the Postgres and MongoDB drills.

## Why not `vault operator raft snapshot`?

That command only works for the integrated Raft storage backend. VoiceOS's single-node Vault uses the **file** backend (see `scripts/vault/vault.hcl`) — a filesystem-level `tar` is the correct backup mechanism.

## What must be backed up

- `/opt/vault/data/` — the KV secrets themselves.
- `/etc/vault.d/` — Vault config (`vault.hcl`, listener config).
- `/opt/vault/init.json` — **unseal keys + initial root token**. Without these, a restored backup is unusable because Vault refuses to serve until unsealed.

⚠️ The archive contains unseal keys in cleartext. It **must** be encrypted at rest (SSE-KMS on S3, GPG offline) and its access strictly limited.

## Preconditions

1. Root or `sudo` on the Vault host.
2. Sufficient disk under `/opt/voiceos/backups/vault/` (small — the file backend is typically < 10 MiB).
3. If using `--seal`, `VAULT_ADDR` + `VAULT_TOKEN` (root or admin) must be set.

## Backup procedure

**Preferred — sealed snapshot (consistent, brief service interruption):**

```bash
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=<root-or-admin-token>
sudo -E bash infra/dr/scripts/backup-vault.sh --seal
```

The script seals Vault, tars data + config + init, unseals via `voiceos-vault-unseal.service` (or you unseal by hand if that unit is not installed).

**Alternate — live snapshot (no downtime, quiescent window only):**

```bash
sudo bash infra/dr/scripts/backup-vault.sh
```

Do this only during low-write windows (nightly). The file backend writes atomically per key, so a tar taken between writes is consistent; a tar taken mid-write of a specific key can miss that key. Sealing eliminates the risk.

Both produce `/opt/voiceos/backups/vault/vault-<timestamp>.tar.gz` and print its SHA256.

**Ship off-box.** A backup collocated with Vault is not a backup. Push to S3/scp immediately.

## Restore procedure

```bash
sudo bash infra/dr/scripts/backup-vault.sh --restore /path/to/vault-YYYYMMDDHHMMSS.tar.gz
```

The script:
1. Stops `vault.service`.
2. Moves the current `/opt/vault/data` aside to `/opt/vault/data.pre-restore-<epoch>` (never deleted — manual cleanup once restore is verified).
3. Extracts the archive at `/`.
4. Restarts `vault.service`.
5. Reports status (expected: `Sealed=true`).

Then unseal:

```bash
systemctl start voiceos-vault-unseal.service
# OR manually, using unseal keys from the restored /opt/vault/init.json:
vault operator unseal <key1>
vault operator unseal <key2>
vault operator unseal <key3>
```

## Success criteria (drill)

- Archive builds without error, `tar -tzf` succeeds.
- SHA256 recorded.
- Restore path (on a scratch VM, not the live host!) restores `/opt/vault/data`, Vault unseals with the archived unseal keys, and `vault kv get secret/voiceos/postgres` returns the expected password.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `tar: /opt/vault/data: Cannot open` | Wrong user / SELinux / AppArmor | Run as root; check `dmesg` |
| Restored Vault stays `Sealed=true` after unseal | Unseal keys in `init.json` don't match | You restored `data/` but not `init.json`, or the keys have been rotated since backup |
| `vault operator unseal` returns "server is not yet initialized" | `data/` extracted into wrong path | Verify `storage "file" { path = "/opt/vault/data" }` in `vault.hcl` matches |

## What this drill does NOT prove

- **Cross-region.** Same as Mongo drill — this runs local, doesn't exercise off-box transfer.
- **Key rotation.** If unseal keys have been rotated since the backup was taken, the archive is unusable. Keep rotation events + a fresh backup lockstepped.
- **Multi-node HA.** Single-node only. HA topology (integrated Raft on 3 nodes) is a separate future migration, not part of the pilot.
