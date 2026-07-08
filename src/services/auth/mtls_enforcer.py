"""mTLSEnforcer — validates client certificates for service-to-service calls
(V4 Ch5 §5.4 mTLS; AR-3 "no plaintext service-to-service calls").

Verifies the presented client certificate's signature against a configured
internal CA (self-managed PKI) and its validity window. No plaintext
service-to-service call is permitted without a certificate that passes both
checks.

Architecture: V4 Ch5 (Authentication — mTLS); AR-3.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509 import Certificate, NameOID, load_pem_x509_certificate

from .models import AuthContext, AuthenticationError, AuthMethod

SERVICE_MESH_TENANT_ID = "system"
"""Sentinel tenant scope for mTLS-authenticated service identities.

Service-to-service calls are infrastructure-scoped, not customer-tenant
scoped (AR-8 governs customer data access, not internal mesh traffic) —
this constant marks that distinction explicitly rather than leaving
``tenant_id`` empty.
"""


class MTLSEnforcer:
    """Validates a client certificate against the internal CA (V4 Ch5 §5.4).

    Args:
        ca_certificate_pem: The internal CA's certificate (PEM), used to
            verify presented client certificates' signatures.
    """

    def __init__(self, ca_certificate_pem: bytes) -> None:
        self._ca_cert: Certificate = load_pem_x509_certificate(ca_certificate_pem)

    def validate_client_cert(self, client_cert_pem: bytes | str) -> AuthContext:
        """Validate a presented client certificate and return its identity.

        Raises:
            AuthenticationError: If the certificate is malformed, expired/
                not-yet-valid, or its signature does not verify against the
                configured CA.
        """
        pem_bytes = client_cert_pem.encode() if isinstance(client_cert_pem, str) else client_cert_pem
        try:
            cert = load_pem_x509_certificate(pem_bytes)
        except ValueError as exc:
            raise AuthenticationError(f"Malformed client certificate: {exc}") from exc

        now = datetime.now(UTC)
        if now < cert.not_valid_before_utc or now > cert.not_valid_after_utc:
            raise AuthenticationError(
                f"Client certificate outside its validity window "
                f"({cert.not_valid_before_utc} .. {cert.not_valid_after_utc})"
            )

        if not self._verify_signed_by_ca(cert):
            raise AuthenticationError("Client certificate signature does not verify against the internal CA")

        service_name = self._extract_common_name(cert)
        return AuthContext(
            subject=service_name,
            tenant_id=SERVICE_MESH_TENANT_ID,
            role="service",
            auth_method=AuthMethod.MTLS,
            expires_at=cert.not_valid_after_utc.timestamp(),
        )

    def _verify_signed_by_ca(self, cert: Certificate) -> bool:
        ca_public_key = self._ca_cert.public_key()
        hash_algorithm = cert.signature_hash_algorithm
        if hash_algorithm is None:
            # A certificate using a signature scheme with no separate hash
            # (e.g. Ed25519) — unsupported by this CA-signature check.
            return False
        try:
            if isinstance(ca_public_key, rsa.RSAPublicKey):
                ca_public_key.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    hash_algorithm,
                )
            elif isinstance(ca_public_key, ec.EllipticCurvePublicKey):
                ca_public_key.verify(
                    cert.signature,
                    cert.tbs_certificate_bytes,
                    ec.ECDSA(hash_algorithm),
                )
            else:
                return False
        except InvalidSignature:
            return False
        return True

    @staticmethod
    def _extract_common_name(cert: Certificate) -> str:
        attributes = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not attributes:
            raise AuthenticationError("Client certificate subject has no Common Name")
        return str(attributes[0].value)
