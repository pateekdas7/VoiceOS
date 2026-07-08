"""AuthService — validates credentials per type and resolves an AuthContext
(V4 Ch5 §5.7 "Authentication Service").

The single entry point every enforcement point (middleware, gRPC
interceptor) consults: given whatever credential headers a request
presented, resolve exactly one :class:`AuthContext` or raise
:class:`AuthenticationError`.

Architecture: V4 Ch5 (Authentication).
"""

from __future__ import annotations

from .api_key_validator import APIKeyValidator
from .jwt_validator import JWTValidator
from .models import AuthContext, AuthenticationError
from .mtls_enforcer import MTLSEnforcer

_BEARER_PREFIX = "bearer "


class AuthService:
    """Resolves an :class:`AuthContext` from whichever credential a request presents.

    Every validator is optional so a service can be configured with only the
    credential types it actually needs to accept (e.g. a pure internal
    service may wire only ``mtls_enforcer``). ``None`` means "this
    credential type is not accepted here" — presenting it still raises
    :class:`AuthenticationError`, it does not silently no-op.
    """

    def __init__(
        self,
        jwt_validator: JWTValidator | None = None,
        api_key_validator: APIKeyValidator | None = None,
        mtls_enforcer: MTLSEnforcer | None = None,
    ) -> None:
        self._jwt_validator = jwt_validator
        self._api_key_validator = api_key_validator
        self._mtls_enforcer = mtls_enforcer

    def authenticate(
        self,
        authorization_header: str | None = None,
        api_key_header: str | None = None,
        client_cert_pem: bytes | str | None = None,
    ) -> AuthContext:
        """Detect the credential type present and validate it.

        Precedence when multiple are somehow present: mTLS (transport-level,
        strongest guarantee) > Bearer JWT > API key.

        Raises:
            AuthenticationError: No credential supplied, an unconfigured
                credential type was presented, or the credential itself
                failed validation.
        """
        if client_cert_pem:
            if self._mtls_enforcer is None:
                raise AuthenticationError("mTLS authentication is not configured on this service")
            return self._mtls_enforcer.validate_client_cert(client_cert_pem)

        if authorization_header and authorization_header.lower().startswith(_BEARER_PREFIX):
            if self._jwt_validator is None:
                raise AuthenticationError("JWT authentication is not configured on this service")
            token = authorization_header[len(_BEARER_PREFIX) :]
            return self._jwt_validator.validate(token)

        if api_key_header:
            if self._api_key_validator is None:
                raise AuthenticationError("API key authentication is not configured on this service")
            return self._api_key_validator.validate(api_key_header)

        raise AuthenticationError("No credentials supplied (missing Bearer token, X-API-Key, or client certificate)")
