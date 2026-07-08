#!/usr/bin/env python3
"""One-time backfill: encrypt existing plaintext PII into the Sprint-019
``*_encrypted`` columns (migration 0016).

Idempotent — skips any row whose ``*_encrypted`` column is already
populated, so it's safe to re-run (e.g. after adding more tenants).

Usage:
  POSTGRES_DSN=... VAULT_ADDR=... VAULT_TOKEN=... python scripts/db/encrypt_pii_backfill.py
"""

from __future__ import annotations

import json
import os
import sys

import psycopg2

from src.libs.encryption.crypto_shred import CryptoShredder
from src.libs.encryption.dek_store import PostgresDEKStore
from src.libs.encryption.envelope import EnvelopeEncryption
from src.libs.encryption.kms_client import VaultTransitKMSClient
from src.libs.encryption.service import EncryptionService


def _build_encryption_service(conn: psycopg2.extensions.connection) -> EncryptionService:
    vault_addr = os.environ["VAULT_ADDR"]
    vault_token = os.environ["VAULT_TOKEN"]
    kms = VaultTransitKMSClient(vault_addr, vault_token)
    dek_store = PostgresDEKStore(conn)
    envelope = EnvelopeEncryption(kms, dek_store)
    return EncryptionService(envelope, CryptoShredder(dek_store))


def backfill_customers(conn: psycopg2.extensions.connection, encryption_service: EncryptionService) -> int:
    cur = conn.cursor()
    cur.execute("SELECT customer_id, tenant_id, name FROM customers WHERE name_encrypted IS NULL AND name IS NOT NULL")
    rows = cur.fetchall()
    for customer_id, tenant_id, name in rows:
        payload = encryption_service.encrypt(name.encode("utf-8"), str(tenant_id))
        cur.execute(
            "UPDATE customers SET name_encrypted = %s WHERE customer_id = %s",
            (payload.to_bytes(), customer_id),
        )
    conn.commit()
    return len(rows)


def backfill_contacts(conn: psycopg2.extensions.connection, encryption_service: EncryptionService) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT contact_id, tenant_id, value FROM customer_contacts WHERE value_encrypted IS NULL AND value IS NOT NULL"
    )
    rows = cur.fetchall()
    for contact_id, tenant_id, value in rows:
        payload = encryption_service.encrypt(value.encode("utf-8"), str(tenant_id))
        cur.execute(
            "UPDATE customer_contacts SET value_encrypted = %s WHERE contact_id = %s",
            (payload.to_bytes(), contact_id),
        )
    conn.commit()
    return len(rows)


def backfill_addresses(conn: psycopg2.extensions.connection, encryption_service: EncryptionService) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT address_id, tenant_id, line1, line2, city, state, pincode "
        "FROM customer_addresses WHERE address_encrypted IS NULL"
    )
    rows = cur.fetchall()
    for address_id, tenant_id, line1, line2, city, state, pincode in rows:
        blob = json.dumps({"line1": line1, "line2": line2, "city": city, "state": state, "pincode": pincode})
        payload = encryption_service.encrypt(blob.encode("utf-8"), str(tenant_id))
        cur.execute(
            "UPDATE customer_addresses SET address_encrypted = %s WHERE address_id = %s",
            (payload.to_bytes(), address_id),
        )
    conn.commit()
    return len(rows)


def main() -> int:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        print("ERROR: POSTGRES_DSN not set", file=sys.stderr)
        return 1

    conn = psycopg2.connect(dsn)
    try:
        encryption_service = _build_encryption_service(conn)
        n_customers = backfill_customers(conn, encryption_service)
        n_contacts = backfill_contacts(conn, encryption_service)
        n_addresses = backfill_addresses(conn, encryption_service)
        print(f"OK: backfilled {n_customers} customers, {n_contacts} contacts, {n_addresses} addresses")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
