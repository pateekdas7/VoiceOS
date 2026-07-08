"""Unit tests for the Authentication service (Sprint-018, V4 Ch5).

All tests run fully in-process: JWTs are signed against an in-process RSA
key pair (no real IdP), mTLS certificates are generated against an
in-process test CA, and the OIDC token exchange uses a fake HTTP transport.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.services.auth.api_key_validator import APIKeyValidator
from src.services.auth.jwt_validator import JWTValidator, issue_test_token
from src.services.auth.middleware import AuthMiddleware
from src.services.auth.models import AuthContext, AuthenticationError, AuthMethod
from src.services.auth.mtls_enforcer import SERVICE_MESH_TENANT_ID, MTLSEnforcer
from src.services.auth.oidc_provider import OIDCProvider
from src.services.auth.service import AuthService

# ---------------------------------------------------------------------------
# Key/cert generation helpers (in-process, no real IdP/CA)
# ---------------------------------------------------------------------------


def _generate_rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_ca() -> tuple[RSAPrivateKey, x509.Certificate]:
    key, _ = _generate_rsa_keypair()
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VoiceOS Test CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _make_client_cert(
    ca_key: RSAPrivateKey,
    ca_cert: x509.Certificate,
    common_name: str,
    not_valid_before: datetime.datetime | None = None,
    not_valid_after: datetime.datetime | None = None,
) -> bytes:
    key, _ = _generate_rsa_keypair()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_valid_before or now - datetime.timedelta(days=1))
        .not_valid_after(not_valid_after or now + datetime.timedelta(days=30))
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _cert_to_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[RSAPrivateKey, RSAPublicKey]:
    return _generate_rsa_keypair()


@pytest.fixture(scope="module")
def ca_keypair() -> tuple[RSAPrivateKey, x509.Certificate]:
    return _make_ca()


# ---------------------------------------------------------------------------
# Required named tests (Sprint-018.md)
# ---------------------------------------------------------------------------


class TestRequiredNamedTests:
    def test_jwt_valid(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        validator = JWTValidator(public_key=public_key)
        token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-1", role="AGENT")

        context = validator.validate(token)

        assert isinstance(context, AuthContext)
        assert context.tenant_id == "tenant-1"
        assert context.role == "AGENT"
        assert context.auth_method == AuthMethod.JWT

    def test_jwt_expired(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        validator = JWTValidator(public_key=public_key)
        token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-1", expires_in_seconds=-10)

        with pytest.raises(AuthenticationError):
            validator.validate(token)


# ---------------------------------------------------------------------------
# JWTValidator — additional coverage
# ---------------------------------------------------------------------------


class TestJWTValidator:
    def test_invalid_signature_rejected(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        _private_key, public_key = rsa_keypair
        other_private_key, _other_public_key = _generate_rsa_keypair()
        validator = JWTValidator(public_key=public_key)
        token = issue_test_token(other_private_key, subject="agent-1", tenant_id="tenant-1")

        with pytest.raises(AuthenticationError):
            validator.validate(token)

    def test_missing_required_claim_rejected(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        import jwt as pyjwt

        private_key, public_key = rsa_keypair
        validator = JWTValidator(public_key=public_key)
        # Missing tenant_id entirely.
        token = pyjwt.encode({"sub": "agent-1", "exp": 9999999999}, key=private_key, algorithm="RS256")

        with pytest.raises(AuthenticationError):
            validator.validate(token)

    def test_issuer_mismatch_rejected(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        validator = JWTValidator(public_key=public_key, issuer="https://idp.expected")
        token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-1", issuer="https://idp.other")

        with pytest.raises(AuthenticationError):
            validator.validate(token)


# ---------------------------------------------------------------------------
# APIKeyValidator
# ---------------------------------------------------------------------------


class TestAPIKeyValidator:
    def test_missing_header_returns_401_equivalent(self) -> None:
        validator = APIKeyValidator()
        with pytest.raises(AuthenticationError):
            validator.validate(None)

    def test_valid_key_resolves_tenant_and_scopes(self) -> None:
        validator = APIKeyValidator()
        validator.register_key("secret-key-123", tenant_id="tenant-1", role="AGENT", scopes=("calls:read",))

        context = validator.validate("secret-key-123")

        assert context.tenant_id == "tenant-1"
        assert context.scopes == ("calls:read",)
        assert context.auth_method == AuthMethod.API_KEY

    def test_unknown_key_rejected(self) -> None:
        validator = APIKeyValidator()
        with pytest.raises(AuthenticationError):
            validator.validate("not-a-real-key")

    def test_keys_never_stored_in_plaintext(self) -> None:
        validator = APIKeyValidator()
        validator.register_key("secret-key-123", tenant_id="tenant-1")
        assert "secret-key-123" not in validator._key_store


# ---------------------------------------------------------------------------
# MTLSEnforcer
# ---------------------------------------------------------------------------


class TestMTLSEnforcer:
    def test_valid_client_cert_authenticates(self, ca_keypair: tuple[RSAPrivateKey, x509.Certificate]) -> None:
        ca_key, ca_cert = ca_keypair
        enforcer = MTLSEnforcer(ca_certificate_pem=_cert_to_pem(ca_cert))
        client_cert_pem = _make_client_cert(ca_key, ca_cert, "policy-engine-service")

        context = enforcer.validate_client_cert(client_cert_pem)

        assert context.subject == "policy-engine-service"
        assert context.tenant_id == SERVICE_MESH_TENANT_ID
        assert context.auth_method == AuthMethod.MTLS

    def test_expired_cert_rejected(self, ca_keypair: tuple[RSAPrivateKey, x509.Certificate]) -> None:
        ca_key, ca_cert = ca_keypair
        enforcer = MTLSEnforcer(ca_certificate_pem=_cert_to_pem(ca_cert))
        now = datetime.datetime.now(datetime.UTC)
        expired_cert_pem = _make_client_cert(
            ca_key,
            ca_cert,
            "auth-service",
            not_valid_before=now - datetime.timedelta(days=10),
            not_valid_after=now - datetime.timedelta(days=1),
        )

        with pytest.raises(AuthenticationError):
            enforcer.validate_client_cert(expired_cert_pem)

    def test_cert_from_untrusted_ca_rejected(self, ca_keypair: tuple[RSAPrivateKey, x509.Certificate]) -> None:
        _ca_key, ca_cert = ca_keypair
        enforcer = MTLSEnforcer(ca_certificate_pem=_cert_to_pem(ca_cert))

        rogue_ca_key, rogue_ca_cert = _make_ca()
        rogue_cert_pem = _make_client_cert(rogue_ca_key, rogue_ca_cert, "attacker-service")

        with pytest.raises(AuthenticationError):
            enforcer.validate_client_cert(rogue_cert_pem)


# ---------------------------------------------------------------------------
# OIDCProvider (mock IdP transport)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_ok: bool = True) -> None:
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("token endpoint returned an error status")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeTokenEndpointClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_call: dict[str, Any] | None = None

    def post(self, url: str, data: dict[str, str]) -> _FakeResponse:
        self.last_call = {"url": url, "data": data}
        return self._response


class TestOIDCProvider:
    def test_exchange_code_returns_auth_context(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        token = issue_test_token(private_key, subject="user-1", tenant_id="tenant-1", role="MANAGER")
        fake_client = _FakeTokenEndpointClient(_FakeResponse({"access_token": token, "token_type": "Bearer"}))
        provider = OIDCProvider(
            token_endpoint="https://idp.example/token",
            client_id="voiceos",
            client_secret="secret",
            http_client=fake_client,
            jwt_validator=JWTValidator(public_key=public_key),
        )

        context = provider.exchange_code("auth-code-abc", redirect_uri="https://voiceos.example/callback")

        assert context.tenant_id == "tenant-1"
        assert context.role == "MANAGER"
        assert fake_client.last_call is not None

    def test_exchange_code_missing_access_token_raises(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        _private_key, public_key = rsa_keypair
        fake_client = _FakeTokenEndpointClient(_FakeResponse({}))
        provider = OIDCProvider(
            token_endpoint="https://idp.example/token",
            client_id="voiceos",
            client_secret="secret",
            http_client=fake_client,
            jwt_validator=JWTValidator(public_key=public_key),
        )

        with pytest.raises(AuthenticationError):
            provider.exchange_code("bad-code", redirect_uri="https://voiceos.example/callback")

    def test_token_endpoint_error_raises_authentication_error(
        self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]
    ) -> None:
        _private_key, public_key = rsa_keypair
        fake_client = _FakeTokenEndpointClient(_FakeResponse({}, status_ok=False))
        provider = OIDCProvider(
            token_endpoint="https://idp.example/token",
            client_id="voiceos",
            client_secret="secret",
            http_client=fake_client,
            jwt_validator=JWTValidator(public_key=public_key),
        )

        with pytest.raises(AuthenticationError):
            provider.exchange_code("bad-code", redirect_uri="https://voiceos.example/callback")


# ---------------------------------------------------------------------------
# AuthService — credential-type dispatch
# ---------------------------------------------------------------------------


class TestAuthService:
    def test_dispatches_bearer_jwt(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        service = AuthService(jwt_validator=JWTValidator(public_key=public_key))
        token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-1")

        context = service.authenticate(authorization_header=f"Bearer {token}")

        assert context.tenant_id == "tenant-1"

    def test_dispatches_api_key(self) -> None:
        api_key_validator = APIKeyValidator()
        api_key_validator.register_key("key-1", tenant_id="tenant-1")
        service = AuthService(api_key_validator=api_key_validator)

        context = service.authenticate(api_key_header="key-1")

        assert context.tenant_id == "tenant-1"

    def test_no_credentials_raises(self) -> None:
        service = AuthService()
        with pytest.raises(AuthenticationError):
            service.authenticate()

    def test_unconfigured_credential_type_raises(self) -> None:
        service = AuthService()  # no jwt_validator configured
        with pytest.raises(AuthenticationError):
            service.authenticate(authorization_header="Bearer sometoken")


# ---------------------------------------------------------------------------
# AuthMiddleware — ASGI integration (Starlette TestClient, Sprint-016 precedent)
# ---------------------------------------------------------------------------


def _build_test_app(auth_service: AuthService) -> Starlette:
    async def protected(request: Request) -> JSONResponse:
        context: AuthContext = request.state.auth_context
        return JSONResponse({"subject": context.subject, "tenant_id": context.tenant_id})

    app = Starlette(routes=[Route("/protected", protected)])
    return AuthMiddleware(app, auth_service)  # type: ignore[return-value]


class TestAuthMiddleware:
    def test_request_without_token_returns_401(self) -> None:
        app = _build_test_app(AuthService())
        client = TestClient(app)

        response = client.get("/protected")

        assert response.status_code == 401

    def test_request_with_valid_token_returns_200(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        private_key, public_key = rsa_keypair
        app = _build_test_app(AuthService(jwt_validator=JWTValidator(public_key=public_key)))
        client = TestClient(app)
        token = issue_test_token(private_key, subject="agent-1", tenant_id="tenant-1")

        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["tenant_id"] == "tenant-1"

    def test_request_with_invalid_token_returns_401(self, rsa_keypair: tuple[RSAPrivateKey, RSAPublicKey]) -> None:
        _private_key, public_key = rsa_keypair
        other_private_key, _other_public_key = _generate_rsa_keypair()
        app = _build_test_app(AuthService(jwt_validator=JWTValidator(public_key=public_key)))
        client = TestClient(app)
        token = issue_test_token(other_private_key, subject="agent-1", tenant_id="tenant-1")

        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
