# Vault failure
1. Check: `systemctl status vault`.
2. Inspect: `journalctl -u vault -n 150 --no-pager`.
3. Probe with the configured address/credentials: `vault status`.
4. Use `infra/dr/runbooks/vault-snapshot.md` for recovery; never expose unseal material in logs.
5. Verify the latest snapshot after recovery.
Environment: CPU/Vault host.