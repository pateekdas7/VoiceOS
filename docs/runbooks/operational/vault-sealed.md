# Runbook: VaultSealed
**Alert:** Vault /v1/sys/health returns sealed=true | **Severity:** critical

## What it means
Vault sealed itself (process restart without auto-unseal). Services that fetch secrets from Vault will fall back to `.env` values — this is safe for now but Vault-dependent features degrade.

## Immediate steps
```bash
# Confirm sealed state
curl -s http://127.0.0.1:8200/v1/sys/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('sealed:', d['sealed'])"

# Unseal (requires the unseal key from init.json)
UNSEAL_KEY=$(sudo python3 -c "import json; print(json.load(open('/opt/vault/init.json'))['unseal_keys_b64'][0])")
VAULT_ADDR=http://127.0.0.1:8200 vault operator unseal "$UNSEAL_KEY"
```

## Verify after unseal
```bash
curl -s http://127.0.0.1:8200/v1/sys/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('sealed:', d['sealed'], '| initialized:', d['initialized'])"
```

## If vault.service is stopped
```bash
sudo systemctl start vault
sleep 5
# Then unseal as above
```

## Escalate if
- `/opt/vault/init.json` is missing — Vault cannot be unsealed without the unseal key
  - If this happens: the Vault data is permanently locked. Restore from Vault snapshot: `vault-snapshot.md`
- Vault seals again within minutes — indicates a process or config issue

## Prevention
The `voiceos-vault-unseal.service` (oneshot, runs at boot) should unseal automatically. If it failed:
```bash
systemctl status voiceos-vault-unseal.service
sudo systemctl start voiceos-vault-unseal.service
```
