#!/usr/bin/env python3
"""Sprint-019 Phase 2 infrastructure validation — run against the real
self-hosted Vault (KV + Transit), Postgres, Redis, MongoDB, and local-disk
object store on the CPU node.

Exercises the scenarios in implementation/sprints/Sprint-019.md's Phase 2
"Integration validation"/"Infrastructure Validation" sections against real
infrastructure. Not part of the pytest suite (pytest coverage of the same
behaviors lives in tests/unit/libs/test_{secrets,encryption,privacy}.py and
tests/integration/libs/test_encryption_integration.py) — this is an
operational smoke-test / evidence script, following the Sprint-013/015/
016/017/018 precedent.

Usage:
    VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=<app-token> \\
    POSTGRES_PASSWORD=<pw> REDIS_PASSWORD=<pw> MONGO_PASSWORD=<pw> \\
    python scripts/sprint019_infra_validation.py

Connection strings are built internally with the password URL-encoded
(``urllib.parse.quote``) — passing raw passwords as separate env vars
avoids every consumer having to hand-construct a URI and get bitten by a
password containing '/'/'@'/etc., which is exactly what happened when this
script first ran against a base64-generated password in Sprint-019 Phase 2.
"""

from __future__ import annotations

import datetime
import os
import sys
import urllib.parse
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import pymongo
import redis as redis_lib

from src.libs.contracts.models.customer import Address, Customer, CustomerContact
from src.libs.contracts.primitives import CustomerId, TenantId
from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.dek_store import PostgresDEKStore
from src.libs.encryption.envelope import EnvelopeEncryption, KeyNotFoundError
from src.libs.encryption.kms_client import VaultTransitKMSClient
from src.libs.encryption.service import EncryptionService
from src.libs.privacy.erasure import DataErasureJob
from src.libs.privacy.minimizer import DataMinimizer
from src.libs.privacy.object_store import LocalDiskObjectStore
from src.libs.repositories.customer import CustomerRepository
from src.libs.secrets.manager import SecretsManager
from src.libs.secrets.providers.vault_provider import HVACVaultClient, VaultProvider

VAULT_ADDR = os.environ["VAULT_ADDR"]
VAULT_TOKEN = os.environ["VAULT_TOKEN"]
RECORDINGS_DIR = os.environ.get("RECORDINGS_DIR", "/opt/voiceos/recordings")

_pg_pw = urllib.parse.quote(os.environ["POSTGRES_PASSWORD"], safe="")
_redis_pw = urllib.parse.quote(os.environ["REDIS_PASSWORD"], safe="")
_mongo_pw = urllib.parse.quote(os.environ["MONGO_PASSWORD"], safe="")

POSTGRES_DSN = f"postgresql://voiceos:{_pg_pw}@localhost:5432/voiceos"
REDIS_URL = f"redis://:{_redis_pw}@localhost:6379/0"
MONGO_URI = f"mongodb://voiceos:{_mongo_pw}@localhost:27017/voiceos?authSource=voiceos"

# data_encryption_keys.tenant_id is a real UUID column (matches the
# customers/customer_contacts convention) — a prefixed string like
# "validation-<uuid>" fails Postgres's UUID input parsing, discovered
# running this against the real schema in Sprint-019 Phase 2.
_TEST_TENANT = str(uuid.uuid4())


class _ConsentChecker:
    def check_consent(self, tenant_id: str, customer_id: str, consent_type: object) -> object:
        class _Consent:
            status = "REVOKED"

        return _Consent()


class _TombstoneStore:
    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self._conn = conn

    def tombstone_pii(self, tenant_id: str, customer_id: str) -> None:
        cur = self._conn.cursor()
        cur.execute("UPDATE customers SET name = 'ERASED' WHERE customer_id = %s", (customer_id,))
        self._conn.commit()


class _CertificateStore:
    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self._conn = conn

    def save(self, certificate: object) -> None:
        cur = self._conn.cursor()
        cur.execute(
            "INSERT INTO data_erasure_certificates "
            "(certificate_id, tenant_id, customer_id, scope, method, completed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                certificate.certificate_id,  # type: ignore[attr-defined]
                certificate.tenant_id,  # type: ignore[attr-defined]
                certificate.customer_id,  # type: ignore[attr-defined]
                list(certificate.scope),  # type: ignore[attr-defined]
                certificate.method,  # type: ignore[attr-defined]
                certificate.completed_at,  # type: ignore[attr-defined]
            ),
        )
        self._conn.commit()


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    vault_client = HVACVaultClient(VAULT_ADDR, VAULT_TOKEN)
    vault_provider = VaultProvider(vault_client)
    secrets_manager = SecretsManager(vault_provider)

    pg_conn = psycopg2.connect(POSTGRES_DSN)
    kms = VaultTransitKMSClient(VAULT_ADDR, VAULT_TOKEN)
    dek_store = PostgresDEKStore(pg_conn)
    envelope = EnvelopeEncryption(kms, dek_store)
    crypto_shredder = CryptoShredder(dek_store)
    encryption_service = EncryptionService(envelope, crypto_shredder)

    print(f"Vault: {VAULT_ADDR}  |  Postgres: connected  |  tenant={_TEST_TENANT}")
    print()

    # 1. SecretsManager fetches a real secret from Vault KV — never os.environ.
    pg_secret = secrets_manager.get_secret("voiceos/postgres")
    results.append(
        ("SecretsManager.get_secret() fetches from real Vault KV", len(pg_secret) > 0, f"len={len(pg_secret)}")
    )

    # 2. Envelope encryption round trip via real Vault Transit.
    payload = encryption_service.encrypt(b"real-vault-roundtrip", _TEST_TENANT)
    decrypted = encryption_service.decrypt(payload, _TEST_TENANT)
    results.append(("EnvelopeEncryption round trip via real Vault Transit", decrypted == b"real-vault-roundtrip", ""))

    # 3. CryptoShredder against the real DEK store -> decrypt raises KeyNotFoundError.
    shred_payload = encryption_service.encrypt(b"to-be-shredded", _TEST_TENANT)
    encryption_service.crypto_shred(_TEST_TENANT, shred_payload.dek_id)
    shred_raised = False
    try:
        encryption_service.decrypt(shred_payload, _TEST_TENANT)
    except KeyNotFoundError:
        shred_raised = True
    results.append(("CryptoShredder.shred() -> decrypt() raises KeyNotFoundError", shred_raised, ""))

    # 4. CustomerRepository writes ciphertext to Postgres, decrypts transparently on read.
    repo = CustomerRepository(pg_conn, encryption_service=encryption_service)
    test_customer = Customer(
        customer_id=CustomerId(str(uuid.uuid4())),
        tenant_id=TenantId(_TEST_TENANT),
        crm_id=f"crm-{uuid.uuid4()}",
        name="Asha Rao",
        contacts=(CustomerContact(contact_type="MOBILE", value="+919876543210", is_primary=True),),
        address=Address(line1="1 MG Road", city="Bengaluru", state="KA", pincode="560001"),
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    repo.create(test_customer)

    raw_cur = pg_conn.cursor()
    raw_cur.execute("SELECT name, name_encrypted FROM customers WHERE customer_id = %s", (test_customer.customer_id,))
    raw_name, raw_name_encrypted = raw_cur.fetchone()
    results.append(
        (
            "customers.name_encrypted column holds ciphertext, not plaintext",
            raw_name_encrypted is not None and bytes(raw_name_encrypted) != raw_name.encode("utf-8"),
            f"plaintext_col={raw_name!r}",
        )
    )

    fetched = repo.get(TenantId(_TEST_TENANT), test_customer.customer_id)
    results.append(
        (
            "CustomerRepository.get() transparently decrypts name/phone/address",
            fetched is not None
            and fetched.name == "Asha Rao"
            and fetched.contacts[0].value == "+919876543210"
            and fetched.address is not None
            and fetched.address.city == "Bengaluru",
            f"name={fetched.name if fetched else None}",
        )
    )

    # 5. Redis auth is enforced (unauthenticated rejected, authenticated works).
    parsed_redis = urllib.parse.urlparse(REDIS_URL)
    noauth_redis_url = f"redis://{parsed_redis.hostname}:{parsed_redis.port}{parsed_redis.path}"
    try:
        redis_lib.Redis.from_url(noauth_redis_url, socket_timeout=2).ping()
        redis_noauth_rejected = False
    except redis_lib.exceptions.AuthenticationError:
        redis_noauth_rejected = True
    redis_authed = redis_lib.Redis.from_url(REDIS_URL, socket_timeout=2)
    results.append(
        (
            "Redis requires auth (unauth rejected, authed PING succeeds)",
            redis_noauth_rejected and redis_authed.ping(),
            "",
        )
    )

    # 6. MongoDB auth is enforced.
    mongo_client: pymongo.MongoClient[dict[str, object]] = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    mongo_db_names = mongo_client["voiceos"].list_collection_names()
    results.append(
        ("MongoDB authenticated connection lists collections", len(mongo_db_names) > 0, f"n={len(mongo_db_names)}")
    )

    # 7. DataMinimizer strips unconsented fields (pure logic, real object).
    minimized = DataMinimizer().minimize(
        {"name": "Asha Rao", "phone": "+919876543210"}, allowed_fields=frozenset({"name"})
    )
    results.append(("DataMinimizer strips phone when consent covers name only", minimized == {"name": "Asha Rao"}, ""))

    # 8. Right-to-erasure end-to-end: consent revoked -> certificate + audio deletion + DEK gone.
    object_store = LocalDiskObjectStore(RECORDINGS_DIR)
    audio_key = f"validation-{uuid.uuid4()}.wav"
    object_store.put(audio_key, b"fake audio bytes")
    erasure_job = DataErasureJob(
        _ConsentChecker(),
        crypto_shredder,
        _TombstoneStore(pg_conn),
        object_store,
        _CertificateStore(pg_conn),
    )
    erasure_result = erasure_job.execute(
        _TEST_TENANT,
        str(test_customer.customer_id),
        "recording",
        dek_ids=(payload.dek_id,),
        audio_object_keys=(audio_key,),
    )
    cert_cur = pg_conn.cursor()
    cert_cur.execute(
        "SELECT COUNT(*) FROM data_erasure_certificates WHERE customer_id = %s",
        (str(test_customer.customer_id),),
    )
    cert_count = cert_cur.fetchone()[0]
    erase_decrypt_raised = False
    try:
        encryption_service.decrypt(payload, _TEST_TENANT)
    except KeyNotFoundError:
        erase_decrypt_raised = True
    results.append(
        (
            "DataErasureJob: certificate persisted + audio deleted + DEK unrecoverable",
            erasure_result.verified and cert_count == 1 and not object_store.exists(audio_key) and erase_decrypt_raised,
            f"cert_count={cert_count}",
        )
    )

    # Cleanup: remove the test customer + its child rows, KEK, and DEK-store rows.
    cleanup_cur = pg_conn.cursor()
    cleanup_cur.execute(
        "DELETE FROM data_erasure_certificates WHERE customer_id = %s", (str(test_customer.customer_id),)
    )
    cleanup_cur.execute("DELETE FROM customers WHERE customer_id = %s", (test_customer.customer_id,))
    pg_conn.commit()
    try:
        vault_client._client.secrets.transit.update_key_configuration(  # type: ignore[attr-defined]
            name=f"tenant-{_TEST_TENANT}", deletion_allowed=True
        )
        vault_client._client.secrets.transit.delete_key(name=f"tenant-{_TEST_TENANT}")  # type: ignore[attr-defined]
    except Exception:
        pass

    print(f"{'CHECK':<70} {'RESULT':<8} DETAIL")
    print("-" * 115)
    all_ok = True
    for name, ok, detail in results:
        status_str = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print(f"{name:<70} {status_str:<8} {detail}")

    pg_conn.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
