"""Unit tests for WebSessionCodec (ADR-005 Sec 3/4.1).

All tests run fully in-process -- no live Postgres/network required (Phase 1).
"""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services.web_api.session import WebSessionCodec


@pytest.fixture(scope="module")
def keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def codec(keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> WebSessionCodec:
    private_key, public_key = keypair
    return WebSessionCodec(private_key, public_key, ttl_seconds=3600)


class TestPlatformSession:
    def test_encode_decode_round_trip(self, codec: WebSessionCodec) -> None:
        token = codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@voiceos.ai", tenant_id=None
        )
        session = codec.decode(token)
        assert session is not None
        assert session.actor_kind == "platform"
        assert session.subject == "pu-1"
        assert session.tenant_id is None
        assert session.role == "PLATFORM_ADMIN"

    def test_encode_refuses_tenant_id_on_platform_session(self, codec: WebSessionCodec) -> None:
        with pytest.raises(ValueError, match="never carry a tenant_id"):
            codec.encode(actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@x.ai", tenant_id="t1")


class TestTenantSession:
    def test_encode_decode_round_trip(self, codec: WebSessionCodec) -> None:
        token = codec.encode(actor_kind="tenant", subject="u-1", role="ADMIN", email="b@tenant.com", tenant_id="t-1")
        session = codec.decode(token)
        assert session is not None
        assert session.actor_kind == "tenant"
        assert session.tenant_id == "t-1"

    def test_encode_requires_tenant_id_on_tenant_session(self, codec: WebSessionCodec) -> None:
        with pytest.raises(ValueError, match="must always carry a tenant_id"):
            codec.encode(actor_kind="tenant", subject="u-1", role="ADMIN", email="b@x.com", tenant_id=None)


class TestSessionValidation:
    def test_decode_rejects_garbage_token(self, codec: WebSessionCodec) -> None:
        assert codec.decode("not-a-real-token") is None

    def test_decode_rejects_token_signed_by_different_key(self, codec: WebSessionCodec) -> None:
        other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_codec = WebSessionCodec(other_private, other_private.public_key())
        forged = other_codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@x.ai", tenant_id=None
        )
        assert codec.decode(forged) is None

    def test_decode_rejects_expired_token(self, keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> None:
        private_key, public_key = keypair
        already_expired_codec = WebSessionCodec(private_key, public_key, ttl_seconds=-1)
        token = already_expired_codec.encode(
            actor_kind="platform", subject="pu-1", role="PLATFORM_ADMIN", email="a@x.ai", tenant_id=None
        )
        assert already_expired_codec.decode(token) is None

    def test_decode_rejects_token_missing_actor_kind(self, codec: WebSessionCodec) -> None:
        import jwt as pyjwt

        private_key = codec._private_key
        bad_payload = {
            "iss": "voiceos-web-bff",
            "iat": time.time(),
            "exp": time.time() + 3600,
            "sub": "pu-1",
            "role": "PLATFORM_ADMIN",
            "email": "a@x.ai",
        }
        token = pyjwt.encode(bad_payload, key=private_key, algorithm="RS256")
        assert codec.decode(token) is None
