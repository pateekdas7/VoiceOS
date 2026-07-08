# Sprint-019 — Secrets Management, Encryption & Privacy Architecture

**Epic:** E5 — Compliance & Security  
**Status:** ⬜ Pending  
**Depends on:** Sprint-017, Sprint-018  
**Blocks:** Sprint-020  

---

## Objective

Implement vault-based secrets management, the complete encryption architecture (at-rest AES-256-GCM, in-transit TLS/SRTP/mTLS, envelope encryption with DEK/KEK, crypto-shredding for erasure), and the privacy architecture (data minimization, purpose limitation, right-to-erasure workflow).

---

## Architecture References

- Volume 4: Ch7 (Secrets Management — vault, runtime injection, rotation, emergency revocation), Ch8 (Encryption — AES-256-GCM, TLS/SRTP/mTLS, envelope encryption, crypto-shredding), Ch9 (Privacy Architecture — minimization, purpose limitation, erasure)
- DocSuite-05: Configuration Reference (secrets, encryption config)

---

## Related Technical Debt — TT-002 (Redis Production Hardening)

> **Added 2026-07-04 (Sprint-013 TT-003 documentation cleanup) — planning note only, does not change this sprint's scope or objectives.**

`implementation/BACKLOG.md` tracks **TT-002 — Redis Production Hardening — Persistence & Eviction Policy**, filed during Sprint-013. The CPU node's Redis instance currently runs apt defaults (`appendonly no`, `maxmemory-policy noeviction`, unchanged since Sprint-003) instead of the V3 Ch4 §4.13 spec (`aof_everysec` persistence + `volatile-ttl` eviction). This is acceptable for the dev/single-node baseline (Redis is explicitly non-authoritative — V3 Ch4 §4.4 — and TTLGuard bounds key lifetime regardless), but must be closed before a production deployment relies on Redis surviving a restart without data loss.

**Sprint-019 is TT-002's primary scheduled closure sprint** (fallback: Sprint-027 — see that sprint's own note). Since Sprint-019 already touches Redis-adjacent secrets/config work, **this sprint's deployment work should also include**:

- Redis persistence hardening: configure `appendonly yes` with `appendfsync everysec` (AOF)
- Appropriate Redis persistence settings for the production topology (AOF rewrite thresholds, RDB snapshot cadence if retained alongside AOF)
- Eviction policy review: set `maxmemory-policy volatile-ttl` (or whichever policy the architecture board approves at that time) and confirm it matches V3 Ch4 §4.13
- Redis durability validation: confirm no data loss on a controlled restart with AOF enabled
- Crash recovery testing: kill `-9` the Redis process and verify AOF replay restores TTL-bounded state correctly
- Persistence benchmarking: measure write-path latency impact of `appendfsync everysec` vs. the current no-persistence baseline (EventBus publish latency, currently 0.39ms — see CPU_NODE_STATE.md §7.4/§17, must stay well under the <50ms/<100ms targets)
- Production configuration validation: confirm the final `redis.conf` matches the documented spec before sign-off
- Deployment updates: `deployment/cpu/bootstrap.sh`/`restore.sh`/`.env.example` reflect the new persistence config
- Infrastructure validation: re-run `scripts/sprint013_infra_validation.py` (or its successor) after the config change to confirm EventBus/DistributedLock/RateLimiter/TTLGuard all still function correctly
- Documentation updates: `deployment/CPU_NODE_STATE.md` §7.1 (persistence config) and §18 (Known Deviations — close out the TT-002 row) once done

If TT-002 is closed here, update its row in `implementation/BACKLOG.md` to **RESOLVED** with the date and verification evidence, mirroring how TT-001 was closed in Sprint-012.

---

## Components to Implement

### `src/libs/secrets/`

```
src/libs/secrets/
├── __init__.py
├── manager.py              (SecretsManager: vault integration)
├── providers/
│   ├── vault_provider.py   (HashiCorp Vault provider)
│   └── aws_secrets_provider.py (AWS Secrets Manager provider — for cloud deploy)
├── rotation.py             (SecretRotator: scheduled rotation with grace window)
└── revocation.py           (EmergencyRevocation: immediate secret invalidation + re-issue)
```

**SecretsManager:**
- `get_secret(path: str) -> str` — fetches secret at runtime from vault; never reads from env vars or config files
- Caches secrets in memory (encrypted) for TTL=300s; re-fetches on expiry
- Grace window on rotation: old secret valid for 60s after new secret is issued
- Emergency revocation: `revoke(path)` → invalidates immediately, triggers re-issue workflow
- Audit: every secret access logs `secret_accessed` audit event (path + actor, not value)
- CI gate: `scripts/check_secrets.py` — scans codebase for hardcoded secrets via regex patterns + trufflehog

### `src/libs/encryption/`

```
src/libs/encryption/
├── __init__.py
├── service.py              (EncryptionService: encrypt/decrypt operations)
├── aes_gcm.py              (AESGCMEncryptor: AES-256-GCM for field-level encryption)
├── envelope.py             (EnvelopeEncryption: DEK encrypted by KEK in KMS)
├── kms_client.py           (KMSClient: AWS KMS / Google Cloud KMS adapter)
├── crypto_shred.py         (CryptoShredder: delete DEK → data is cryptographically erased)
└── tls_config.py           (TLSConfig: TLS 1.3 enforcement, cipher suites, SRTP config)
```

**EnvelopeEncryption:**
- Each tenant has a KEK (Key Encryption Key) managed in KMS
- Each data record has a DEK (Data Encryption Key) encrypted by KEK
- `encrypt(plaintext: bytes, tenant_id: str) -> EncryptedPayload(ciphertext, encrypted_dek, iv, tag)`
- `decrypt(payload: EncryptedPayload, tenant_id: str) -> bytes` — fetches DEK from KMS, decrypts

**CryptoShredder:**
- `shred(tenant_id: str, record_id: str) -> None` — deletes DEK from KMS
- After shredding: data in Postgres/MongoDB is ciphertext with no accessible key → effectively erased
- Writes `DataErasureCertificate` to Postgres (tombstone record proving erasure)

**AES-256-GCM field encryption:**
- Applied to PII fields in Postgres: phone, name, address, UPI_ID
- Encryption key: DEK for that record's tenant
- IV: randomly generated per-encrypt, stored alongside ciphertext

### `src/libs/privacy/`

```
src/libs/privacy/
├── __init__.py
├── engine.py               (PrivacyEngine: purpose check, minimization filter)
├── purpose_registry.py     (PurposeRegistry: maps data fields to allowed purposes)
├── minimizer.py            (DataMinimizer: strips fields not covered by consent)
├── erasure.py              (DataErasureJob: orchestrates right-to-erasure workflow)
└── retention.py            (RetentionScheduler: flags data past retention period)
```

**DataErasureJob (right to erasure):**
1. Verify consent revocation (ConsentRepository)
2. `CryptoShredder.shred(tenant_id, customer_id)` — delete DEK(s)
3. Overwrite non-encrypted PII fields with tombstone ("ERASED")
4. Delete audio recordings from object storage
5. Write `DataErasureCertificate` (Postgres)
6. Emit `DataErasureCompleted` event

---

## Files Expected to Change

**New:** `src/libs/secrets/`, `src/libs/encryption/`, `src/libs/privacy/`  
**New:** `scripts/check_secrets.py` (trufflehog integration in CI)  
**New:** `tests/unit/libs/test_encryption.py`, `test_secrets.py`, `test_privacy.py`  
**New:** `tests/integration/libs/test_encryption_integration.py`  
**Modified:** Postgres PII columns — add encrypted variants (migration in Sprint-014 must be extended)

---

## Acceptance Criteria

- [ ] `SecretsManager.get_secret()` fetches from vault at runtime; never reads hardcoded values
- [ ] Secrets scan in CI: hardcoded credential in test file → CI fails
- [ ] `EnvelopeEncryption.encrypt()` → `decrypt()` round-trip returns original plaintext
- [ ] `CryptoShredder.shred()` → subsequent `decrypt()` raises `KeyNotFoundError` (DEK deleted)
- [ ] After erasure: DataErasureCertificate exists in Postgres; audio file deleted from object storage
- [ ] `DataMinimizer` strips fields not covered by consent purpose
- [ ] TLS 1.3 is enforced for all HTTPS endpoints (TLSConfig applied to all servers)

---

## Required Tests

**Unit:**
- `test_envelope_encrypt_decrypt_roundtrip` — encrypt then decrypt → original plaintext
- `test_crypto_shred_prevents_decrypt` — shred DEK → decrypt raises KeyNotFoundError
- `test_data_minimizer_strips_unconsented_fields` — consent covers name only → phone stripped
- `test_secrets_manager_no_env_vars` — SecretsManager never reads os.environ for secrets
- `test_secrets_scan_catches_hardcoded` — plant a fake secret in a temp file → scanner finds it

**Integration:**
- `test_erasure_workflow_end_to_end` — consent revoke → DataErasureJob → certificate created, audio deleted

---

## Definition of Done

- [ ] All AC items checked
- [ ] Secrets scan clean on codebase
- [ ] All PII Postgres columns use field-level encryption (verified by column inspection)
- [ ] CI green (secrets scan added to CI pipeline)
- [ ] `CHANGELOG.md`, `BACKLOG.md`, `DONE.md`, `PROJECT_STATUS.md` updated
- [ ] `CURRENT_SPRINT.md` updated to Sprint-020

---

## Phase 1 — Local Development & Mock Validation

> **No CPU or GPU infrastructure is required for this phase.** Encryption tests use in-process AES-GCM; KMS uses a `FakeKMSClient`; secrets manager uses a local vault fixture or `FakeVaultClient`.

### Files Created

- `src/libs/secrets/__init__.py`, `manager.py`, `rotation.py`, `revocation.py`
- `src/libs/secrets/providers/vault_provider.py`, `aws_secrets_provider.py`
- `src/libs/encryption/__init__.py`, `service.py`, `aes_gcm.py`, `envelope.py`, `kms_client.py`, `crypto_shred.py`, `tls_config.py`
- `src/libs/privacy/__init__.py`, `engine.py`, `purpose_registry.py`, `minimizer.py`, `erasure.py`, `retention.py`
- `scripts/check_secrets.py` (trufflehog integration)
- `tests/unit/libs/test_encryption.py`, `test_secrets.py`, `test_privacy.py`
- `tests/integration/libs/test_encryption_integration.py`

### Mock Backends Used

| Backend | Mock | How |
|---|---|---|
| HashiCorp Vault | `FakeVaultClient` returning test secrets | Injected into SecretsManager |
| KMS | `FakeKMSClient` returning random DEK bytes | Injected into EnvelopeEncryption |
| Object storage (audio) | `FakeObjectStore` | Simulates audio recording deletion during erasure |

### Validations

| Check | Command | Expected |
|---|---|---|
| Static analysis | `ruff check src/ tests/` | 0 errors |
| Formatting | `ruff format --check src/ tests/` | All files formatted |
| Type checking | `mypy --strict src/ tests/` | 0 issues |
| Boundary check | `python scripts/check_boundaries.py` | 0 violations |
| Secrets scan | `python scripts/check_secrets.py src/` | 0 hardcoded secrets detected |
| Unit tests | `pytest tests/unit/libs/test_encryption.py tests/unit/libs/test_secrets.py tests/unit/libs/test_privacy.py` | All pass |
| Integration test | `pytest tests/integration/libs/test_encryption_integration.py` | Passes |
| Coverage | `pytest --cov=src --cov-report=term-missing` | ≥ 85% |

### Expected Outputs

- `EnvelopeEncryption.encrypt()` → `decrypt()` round-trip returns original plaintext
- `CryptoShredder.shred()` → subsequent `decrypt()` raises `KeyNotFoundError`
- `DataMinimizer`: consent covers `name` only → `phone` field stripped from output
- `SecretsManager.get_secret()`: never reads `os.environ` for secrets
- Secrets scan: plant fake API key in temp file → scanner finds it (test of scanner itself)

---

## Phase 2 — Deployment & Real Infrastructure Validation

> Phase 2 begins only after Phase 1 passes completely.

### CPU Node

**Services integrated/deployed this sprint:**

| Action | Target | Why |
|---|---|---|
| Deploy SecretsManager | All existing services (rolling restarts) | Secrets now fetched from Vault at runtime; env vars no longer used for secrets |
| Wire EnvelopeEncryption | CustomerRepository, ConsentRepository | PII fields encrypted at rest in Postgres |
| Wire PIITokenizer | All services writing customer data | Reversible tokenization for PII in event payloads |
| Deploy PrivacyEngine | ConversationEngine + all data-writing services | Purpose limitation + data minimization enforced |
| Add secrets scan to CI | `.github/workflows/ci.yml` | Hardcoded secret detection in CI pipeline |

**Previously deployed services that remain running:**
- All Sprint-004–018 services

**Deployment procedure:**
1. Provision Vault (or AWS Secrets Manager) and seed all application secrets
2. Run Postgres migration to add `_encrypted` columns for PII fields (phone, name, address)
3. Rolling restart all services with SecretsManager wiring (secrets now from Vault)
4. Encrypt existing PII rows: `python scripts/db/encrypt_pii_backfill.py` (one-time)
5. Verify: `SELECT phone FROM customers LIMIT 1` → ciphertext (not plaintext)

**Health checks:**
- All services: `GET /health/ready` → 200 (Vault reachable for secret fetch)
- Vault audit log: every secret access logs `secret_accessed` event
- `check_secrets.py` in CI: runs on every PR; blocks merge if hardcoded secrets found

**Integration validation:**
- Encrypt → decrypt round-trip via API test on real KMS
- Right-to-erasure: trigger `DataErasureJob` for test customer → `DataErasureCertificate` created in Postgres, audio deleted from object store, `decrypt()` raises `KeyNotFoundError`
- TLS: all HTTPS endpoints enforce TLS 1.3 (verify with `curl --tlsv1.3`)

**Rollback procedure:**
- Encrypted columns: each PII column has both `phone` (original) and `phone_encrypted` during transition; rollback reads from original
- Vault: previous secrets remain valid during rotation grace window (60s)

### GPU Node

> **GPU node is not required during this sprint.** Previously deployed GPU services remain running unchanged. GPU services will use SecretsManager for model-serving API keys in a future sprint.

### Infrastructure Validation

**CPU Validation:**
- PII columns: `SELECT phone FROM customers LIMIT 5` → all values are ciphertext (AES-GCM, not plaintext)
- Erasure: `DataErasureCertificate` table has entry after erasure; audio file deleted from object store
- SecretsManager: application restart → secrets fetched from Vault (verify via Vault audit log)
- CI: introduce test hardcoded secret → PR CI fails at secrets scan step

**GPU Validation:**
> Not applicable this sprint.

**Networking Validation:**
- Vault → Services: secret fetch latency < 50ms (cached in memory for 300s TTL)
- TLS 1.3: all HTTPS endpoints verified with `openssl s_client -tls1_3`
- mTLS service calls: certificates renewed via SecretsManager (not hardcoded)

### Regression Validation

- Walking skeleton e2e test: `pytest tests/e2e/test_walking_skeleton.py` — passes with encrypted Postgres columns
- Auth middleware: JWT still valid after auth secrets moved to Vault
- PolicyEngineService: RBI rules still evaluated correctly after secrets migration

---

## Completion Criteria

**Phase 1 — Local Development & Mock Validation:**
- [ ] SecretsManager (Vault + AWS provider) implemented
- [ ] EnvelopeEncryption (AES-256-GCM + DEK/KEK) implemented
- [ ] CryptoShredder (DEK deletion = data erasure) implemented
- [ ] PrivacyEngine (minimizer, erasure job, retention scheduler) implemented
- [ ] Secrets scan script integrated into CI
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`: all pass
- [ ] Encrypt/decrypt round-trip passes; shred → decrypt raises
- [ ] Coverage ≥ 85%
- [ ] All documentation updated

**Phase 2 — Deployment & Real Infrastructure Validation:**
- [ ] All services fetch secrets from Vault at runtime
- [ ] PII columns encrypted at rest in Postgres (verified by column inspection)
- [ ] Right-to-erasure: DataErasureCertificate created, audio deleted, decrypt raises
- [ ] TLS 1.3 enforced on all HTTPS endpoints
- [ ] CI secrets scan active (blocks hardcoded secrets)
- [ ] All regression tests pass
- [ ] Deployment remains active as baseline for Sprint-020

---

## Infrastructure Snapshot

> Complete after Phase 2 passes. Both documents must reflect the **entire** node state. Sprint-019 fundamentally changes how secrets are managed — the rebuild procedure must reflect Vault-based injection.

### CPU_NODE_STATE.md — Updates This Sprint

- **§11 Environment Variables:** Update ALL entries to note "Value fetched from Vault at runtime (Sprint-019+). No hardcoded values." Add `VAULT_ADDR` and `VAULT_TOKEN` entries.
- Update ALL services in §8.1: note they now fetch secrets from Vault on startup (rolling restart applied)
- Update §10 Volumes: add `audio-recordings` backed by S3/GCS object store; add KMS key path for each tenant's DEK
- Add `PrivacyEngine`, `SecretsManager`, `EncryptionService` as library packages embedded in all services (§8.1 note)
- Update §12 Database Schema: note PII columns encrypted (`phone_encrypted`, `name_encrypted`, etc.)
- Add to §8.3 Startup Order: Vault must be reachable before any service starts

### GPU_NODE_STATE.md — Updates This Sprint

- Update §12 Environment Variables: add `VAULT_ADDR`, `VAULT_TOKEN` — GPU services also fetch secrets from Vault
- Update §16 Rollback Procedure: note secrets rotation grace window (60s)
- `GPU_NODE_STATE.md` last_updated: Sprint-019

### Scripts to Update

| File | Change |
|---|---|
| `deployment/cpu/.env.example` | Add `VAULT_ADDR`, `VAULT_TOKEN`; annotate all other vars as "Vault-managed from Sprint-019" |
| `deployment/gpu/.env.example` | Add `VAULT_ADDR`, `VAULT_TOKEN` |
| `deployment/cpu/restore.sh` | Add: verify Vault reachable before starting services; fetch initial secrets from Vault |
| `deployment/gpu/restore.sh` | Add: verify Vault reachable before starting GPU services |

### DR Validation

**Vault connectivity (critical for rebuild):**
```bash
# On fresh CPU server: Vault must be reachable before restore
curl -sf "$VAULT_ADDR/v1/sys/health" && echo "Vault: OK"
vault kv get secret/voiceos/postgres  # verify at least one secret accessible
```

**CPU node rebuild test:**
```bash
bash deployment/cpu/restore.sh
# Expected: all services start using Vault-fetched secrets (no env var fallback)
# Verify: no plaintext secrets in any service log
```

**PII encryption verification:**
```bash
psql -h $POSTGRES_HOST -U voiceos -d voiceos -c "SELECT phone FROM customers LIMIT 1;"
# Expected: ciphertext (AES-GCM), not plaintext phone number
```

**Post-rebuild regression:**
```bash
pytest tests/integration/ -m regression -v
# Expected: all tests pass; CI secrets scan clean
```
