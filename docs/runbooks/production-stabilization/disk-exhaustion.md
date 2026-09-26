# Disk exhaustion

```bash
df -h
df -i
```
Identify the filesystem and largest safe-to-remove artifacts. Rotate/compress logs first. Never delete database/Vault data or backups as an emergency shortcut. Recheck health and disk alerts after cleanup.