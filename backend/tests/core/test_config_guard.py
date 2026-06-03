"""Production secret fail-fast: refuse to start with default secrets in
production, but keep development working with the docker-compose defaults."""

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
    # _env_file=None: ignore the container's .env so the test is hermetic.
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _build(
            ENVIRONMENT="production", JWT_SECRET="change-me-to-a-long-random-string-min-32-bytes"
        )


def test_production_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _build(ENVIRONMENT="production", JWT_SECRET="too-short")


def test_production_rejects_default_minio_creds() -> None:
    with pytest.raises(ValidationError, match="minioadmin"):
        _build(
            ENVIRONMENT="production", MINIO_ACCESS_KEY="minioadmin", MINIO_SECRET_KEY="minioadmin"
        )


def test_production_rejects_default_db_password() -> None:
    with pytest.raises(ValidationError, match="DB password"):
        _build(ENVIRONMENT="production", DATABASE_URL_APP=_DEV_DB_APP)


def test_production_accepts_strong_secrets() -> None:
    s = _build(ENVIRONMENT="production")  # all strong → no raise
    assert s.ENVIRONMENT == "production"


def test_development_allows_defaults() -> None:
    # Dev keeps working with the docker-compose defaults — guard is skipped.
    s = _build(
        ENVIRONMENT="development",
        JWT_SECRET="change-me-to-a-long-random-string-min-32-bytes",
        MINIO_ACCESS_KEY="minioadmin",
        MINIO_SECRET_KEY="minioadmin",
        DATABASE_URL_APP=_DEV_DB_APP,
        DATABASE_URL_SUPPORT=_DEV_DB_SUPPORT,
    )
    assert s.ENVIRONMENT == "development"
