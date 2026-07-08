"""Unit tests for src/libs/api_security/ (V4 Ch12)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from src.libs.api_security.cors import CORSPolicy
from src.libs.api_security.headers import SecurityHeaders
from src.libs.api_security.input_validator import InputValidator, RequestValidationError
from src.libs.api_security.rate_limiter import APIRateLimiter
from src.libs.redis_client.rate_limiter import RateLimiter
from tests.fixtures.redis import FakeRedisClient


class _EchoSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class TestSecurityHeaders:
    def test_security_headers_include_hsts_and_csp(self) -> None:
        headers = SecurityHeaders()
        response_headers: dict[str, str] = {}

        headers.apply(response_headers)

        assert "Strict-Transport-Security" in response_headers
        assert "Content-Security-Policy" in response_headers
        assert response_headers["X-Frame-Options"] == "DENY"
        assert response_headers["X-Content-Type-Options"] == "nosniff"


class TestCORSPolicy:
    def test_wildcard_origin_is_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="wildcard"):
            CORSPolicy(frozenset({"*"}))

    def test_allowlisted_origin_gets_headers(self) -> None:
        policy = CORSPolicy(frozenset({"https://app.voiceos.example"}))

        headers = policy.headers_for("https://app.voiceos.example")

        assert headers["Access-Control-Allow-Origin"] == "https://app.voiceos.example"

    def test_non_allowlisted_origin_gets_no_headers(self) -> None:
        policy = CORSPolicy(frozenset({"https://app.voiceos.example"}))

        assert policy.headers_for("https://evil.example") == {}


class TestInputValidator:
    def test_rejects_oversized_payload(self) -> None:
        validator = InputValidator(max_payload_bytes=10)

        with pytest.raises(RequestValidationError):
            validator.validate(b'{"message": "this is way too long"}', _EchoSchema)

    def test_rejects_unknown_fields(self) -> None:
        validator = InputValidator()

        with pytest.raises(RequestValidationError):
            validator.validate(b'{"message": "hi", "extra": "nope"}', _EchoSchema)

    def test_accepts_valid_payload(self) -> None:
        validator = InputValidator()

        validated = validator.validate(b'{"message": "hi"}', _EchoSchema)

        assert validated.data == {"message": "hi"}


class TestAPIRateLimiter:
    def test_check_allows_then_blocks_over_limit(self) -> None:
        limiter = APIRateLimiter(RateLimiter(FakeRedisClient()), requests_per_second=2)

        first = limiter.check("tenant-a")
        second = limiter.check("tenant-a")
        third = limiter.check("tenant-a")

        assert first.allowed is True
        assert second.allowed is True
        assert third.allowed is False
