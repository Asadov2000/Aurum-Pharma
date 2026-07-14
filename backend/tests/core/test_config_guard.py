"""Production config guard tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_STRONG_SECRET = "x" * 40
_DEV_DB_APP = "postgresql+asyncpg://app:aurum_app_pw@postgres/aurum"
_DEV_DB_SUPPORT = "postgresql+asyncpg://support:aurum_support_pw@postgres/aurum"
_PROD_DB_APP = "postgresql+asyncpg://app:Str0ng-App-Pw@db/aurum"
_PROD_DB_SUPPORT = "postgresql+asyncpg://support:Str0ng-Sup-Pw@db/aurum"


def _build(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "DATABASE_URL_APP": _PROD_DB_APP,
        "DATABASE_URL_SUPPORT": _PROD_DB_SUPPORT,
        "JWT_SECRET": _STRONG_SECRET,
        "MINIO_ACCESS_KEY": "real-key",
        "MINIO_SECRET_KEY": "real-secret",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _build(
            ENVIRONMENT="production",
            JWT_SECRET="change-me-to-a-long-random-string-min-32-bytes",
        )


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _build(ENVIRONMENT="production", JWT_SECRET="too-short")


def test_production_rejects_default_minio_creds() -> None:
    with pytest.raises(ValidationError, match="minioadmin"):
        _build(
            ENVIRONMENT="production",
            MINIO_ACCESS_KEY="minioadmin",
            MINIO_SECRET_KEY="minioadmin",
        )


def test_production_rejects_default_db_password() -> None:
    with pytest.raises(ValidationError, match="DB password"):
        _build(ENVIRONMENT="production", DATABASE_URL_APP=_DEV_DB_APP)


def test_production_accepts_strong_secrets() -> None:
    s = _build(ENVIRONMENT="production")
    assert s.ENVIRONMENT == "production"
    assert s.refresh_cookie_secure is True


def test_production_rejects_development_edge_transport() -> None:
    with pytest.raises(ValidationError, match="production requires mTLS"):
        _build(ENVIRONMENT="production", EDGE_SYNC_ENABLED=True)


def test_edge_credential_is_secret_in_settings_representation() -> None:
    raw = "edge_v1.00000000-0000-4000-8000-000000000000." + "a" * 64
    settings = _build(EDGE_SYNC_CREDENTIAL=raw)
    assert raw not in repr(settings)
    assert settings.EDGE_SYNC_CREDENTIAL is not None
    assert settings.EDGE_SYNC_CREDENTIAL.get_secret_value() == raw


def test_production_rejects_cross_site_refresh_cookie_without_secure_flag() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SAMESITE"):
        _build(
            ENVIRONMENT="production",
            REFRESH_COOKIE_SAMESITE="none",
            REFRESH_COOKIE_SECURE=False,
        )


def test_development_allows_defaults() -> None:
    s = _build(
        ENVIRONMENT="development",
        JWT_SECRET="change-me-to-a-long-random-string-min-32-bytes",
        MINIO_ACCESS_KEY="minioadmin",
        MINIO_SECRET_KEY="minioadmin",
        DATABASE_URL_APP=_DEV_DB_APP,
        DATABASE_URL_SUPPORT=_DEV_DB_SUPPORT,
    )
    assert s.ENVIRONMENT == "development"
    assert s.refresh_cookie_secure is False
