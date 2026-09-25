# Vault failure

The current backend is **file**, not integrated Raft.

```bash
vault status
systemctl status vault
journalctl -u vault -n 100 --no-pager
systemctl start voiceos-vault-unseal.service
vault status
```
For restore use `sudo bash infra/dr/scripts/backup-vault.sh --restore /path/to/archive.tar.gz`. Treat `/opt/vault/init.json` as sensitive recovery material.