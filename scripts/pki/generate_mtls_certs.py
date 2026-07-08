#!/usr/bin/env python3
"""Provision the internal self-managed PKI for inter-service mTLS (V4 Ch5 §5.4).

Generates a CA keypair/certificate and one leaf certificate per VoiceOS
service, all under a single output directory (default
``/opt/voiceos/certs/``). Idempotent: re-running regenerates all
certificates from scratch (a fresh CA + fresh leaf certs) — this is a
provisioning script for fresh-node bootstrap, not an incremental rotation
tool (certificate rotation is a Sprint-019/Sprint-026 concern, see
Sprint-018.md rollback procedure: "PKI rotation does not affect running
connections immediately").

Usage:
    python3 scripts/pki/generate_mtls_certs.py [--out-dir /opt/voiceos/certs]
"""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CA_VALIDITY_DAYS = 3650
LEAF_VALIDITY_DAYS = 365

SERVICES = (
    "auth-service",
    "authz-service",
    "ai-governance-service",
    "policy-engine-service",
    "conversation-engine",
)


def _generate_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VoiceOS Internal CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _generate_leaf_cert(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, service_name: str
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=LEAF_VALIDITY_DAYS))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(service_name)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _write_pem_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)


def _write_pem_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/opt/voiceos/certs", help="Certificate output directory")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_key, ca_cert = _generate_ca()
    _write_pem_key(out_dir / "ca.key", ca_key)
    _write_pem_cert(out_dir / "ca.crt", ca_cert)
    print(f"OK: CA cert/key written to {out_dir}/ca.crt, {out_dir}/ca.key")

    for service_name in SERVICES:
        leaf_key, leaf_cert = _generate_leaf_cert(ca_key, ca_cert, service_name)
        _write_pem_key(out_dir / f"{service_name}.key", leaf_key)
        _write_pem_cert(out_dir / f"{service_name}.crt", leaf_cert)
        print(f"OK: leaf cert/key written for '{service_name}'")

    print(f"\nProvisioned CA + {len(SERVICES)} service certificates under {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
