"""Unit tests for the Web BFF production entrypoint's config validation
(ADR-005 Sec 4.1). Does not require Postgres -- these assert the fail-fast
behavior *before* any connection is attempted.
"""

from __future__ import annotations

import pytest

from src.services.web_api.main import MissingConfigError, create_app


class TestConfigValidation:
    def test_raises_when_postgres_dsn_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POSTGRES_DSN", raising=False)
        with pytest.raises(MissingConfigError, match="POSTGRES_DSN"):
            create_app()

    def test_raises_when_google_client_id_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_DSN", "postgresql://u:p@localhost/db")
        monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
        with pytest.raises(MissingConfigError, match="GOOGLE_CLIENT_ID"):
            create_app()

    def test_raises_when_frontend_base_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POSTGRES_DSN", "postgresql://u:p@localhost/db")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.delenv("FRONTEND_BASE_URL", raising=False)
        with pytest.raises(MissingConfigError, match="FRONTEND_BASE_URL"):
            create_app()
