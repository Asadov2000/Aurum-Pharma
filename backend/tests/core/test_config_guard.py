"""Production config guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_STRONG_SECRET = "x" * 40
_DEV_DB_APP = "postgresql+asyncpg://app:aurum_app_pw@postgres/aurum"
_DEV_DB_SUPPORT = "postgresql+asyncpg://support:aurum_support_pw@postgres/aurum"
_PROD_DB_APP = "postgresql+asyncpg://app:Str0ng-App-Pw@db/aurum"
_PROD_DB_SUPPORT = "postgresql+asyncpg://support:Str0ng-Sup-Pw@db/aurum"
_METRICS_TOKEN = "m" * 40
_MFA_ENCRYPTION_KEY = "k" * 40
_PREVIOUS_MFA_ENCRYPTION_KEY = "p" * 40
_PRODUCTION_ORIGIN = "https://pharmacy.example.com"


def _build(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "DATABASE_URL_APP": _PROD_DB_APP,
        "DATABASE_URL_SUPPORT": _PROD_DB_SUPPORT,
        "JWT_SECRET": _STRONG_SECRET,
        "MINIO_ACCESS_KEY": "application-user-key",
        "MINIO_SECRET_KEY": "m" * 40,
        "METRICS_TOKEN": _METRICS_TOKEN,
        "MFA_ENCRYPTION_KEY": _MFA_ENCRYPTION_KEY,
        "REDIS_URL": "redis://:Str0ng-Redis-Pw@redis:6379/0",
        "CORS_ORIGINS": [_PRODUCTION_ORIGIN],
        "TRUSTED_HOSTS": ["pharmacy.example.com"],
        "TRUSTED_PROXY_IPS": ["172.30.0.10"],
        "REFRESH_COOKIE_SECURE": True,
        "EMAIL_HOST": "smtp.example.com",
        "EMAIL_USER": "aurum@example.com",
        "EMAIL_PASSWORD": "smtp-provider-token",
        "EMAIL_FROM": "no-reply@example.com",
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
    with pytest.raises(ValidationError, match="MINIO_ACCESS_KEY"):
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


def test_production_rejects_edge_writer_activation() -> None:
    with pytest.raises(ValidationError, match="production Edge security stack"):
        _build(ENVIRONMENT="production", EDGE_WRITER_ACTIVATION_ENABLED=True)


def test_production_rejects_edge_writer_readiness() -> None:
    with pytest.raises(ValidationError, match="complete production Edge bootstrap"):
        _build(ENVIRONMENT="production", EDGE_WRITER_READINESS_ENABLED=True)


def test_edge_credential_is_secret_in_settings_representation() -> None:
    raw = "edge_v1.00000000-0000-4000-8000-000000000000." + "a" * 64
    settings = _build(EDGE_SYNC_CREDENTIAL=raw)
    assert raw not in repr(settings)
    assert settings.EDGE_SYNC_CREDENTIAL is not None
    assert settings.EDGE_SYNC_CREDENTIAL.get_secret_value() == raw


def test_production_rejects_refresh_cookie_without_secure_flag() -> None:
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        _build(
            ENVIRONMENT="production",
            REFRESH_COOKIE_SECURE=False,
        )


@pytest.mark.parametrize(
    "origin",
    [
        "http://pharmacy.example.com",
        "https://localhost",
        "https://user:password@pharmacy.example.com",
        "https://pharmacy.example.com/path",
        "https://[invalid",
    ],
)
def test_non_development_rejects_unsafe_cors_origins(origin: str) -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _build(ENVIRONMENT="staging", CORS_ORIGINS=[origin])


def test_non_development_rejects_untrusted_host_configuration() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        _build(ENVIRONMENT="production", TRUSTED_HOSTS=["*"])

    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        _build(ENVIRONMENT="production", TRUSTED_HOSTS=["other.example.com"])


def test_non_development_rejects_unsafe_proxy_configuration() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_IPS"):
        _build(ENVIRONMENT="production", TRUSTED_PROXY_IPS=[])

    with pytest.raises(ValidationError, match="must not trust every source"):
        _build(ENVIRONMENT="production", TRUSTED_PROXY_IPS=["0.0.0.0/0"])


def test_non_development_requires_redis_authentication() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        _build(ENVIRONMENT="production", REDIS_URL="redis://redis:6379/0")

    with pytest.raises(ValidationError, match="REDIS_URL"):
        _build(ENVIRONMENT="production", REDIS_URL="redis://:[invalid")


def test_non_development_requires_secure_smtp() -> None:
    with pytest.raises(ValidationError, match="SMTP"):
        _build(ENVIRONMENT="production", EMAIL_PASSWORD="")

    with pytest.raises(ValidationError, match="EMAIL_USE_TLS"):
        _build(ENVIRONMENT="production", EMAIL_USE_TLS=False)


def test_credentials_are_hidden_from_settings_representation() -> None:
    settings = _build()
    representation = repr(settings)

    for secret in (
        _PROD_DB_APP,
        _PROD_DB_SUPPORT,
        _STRONG_SECRET,
        "Str0ng-Redis-Pw",
        "application-user-key",
        "m" * 40,
        "smtp-provider-token",
    ):
        assert secret not in representation


def test_production_rejects_missing_metrics_token() -> None:
    with pytest.raises(ValidationError, match="METRICS_TOKEN"):
        _build(ENVIRONMENT="production", METRICS_TOKEN=None)


def test_production_rejects_short_metrics_token() -> None:
    with pytest.raises(ValidationError, match="METRICS_TOKEN"):
        _build(ENVIRONMENT="production", METRICS_TOKEN="too-short")


def test_staging_rejects_missing_metrics_token() -> None:
    with pytest.raises(ValidationError, match="METRICS_TOKEN"):
        _build(ENVIRONMENT="staging", METRICS_TOKEN=None)


def test_non_development_requires_independent_mfa_encryption_key() -> None:
    with pytest.raises(ValidationError, match="MFA_ENCRYPTION_KEY"):
        _build(ENVIRONMENT="staging", MFA_ENCRYPTION_KEY=None)

    with pytest.raises(ValidationError, match="differ from JWT_SECRET"):
        _build(
            ENVIRONMENT="production",
            MFA_ENCRYPTION_KEY=_STRONG_SECRET,
        )


def test_mfa_encryption_key_is_secret_in_settings_representation() -> None:
    settings = _build(
        MFA_ENCRYPTION_KEY=_MFA_ENCRYPTION_KEY,
        MFA_ENCRYPTION_KEY_VERSION=2,
        MFA_ENCRYPTION_PREVIOUS_KEYS={1: _PREVIOUS_MFA_ENCRYPTION_KEY},
    )
    assert _MFA_ENCRYPTION_KEY not in repr(settings)
    assert _PREVIOUS_MFA_ENCRYPTION_KEY not in repr(settings)
    assert settings.MFA_ENCRYPTION_KEY is not None
    assert settings.MFA_ENCRYPTION_KEY.get_secret_value() == _MFA_ENCRYPTION_KEY


def test_mfa_keyring_rejects_ambiguous_or_reused_roots() -> None:
    with pytest.raises(ValidationError, match="must not contain the current version"):
        _build(
            MFA_ENCRYPTION_KEY_VERSION=2,
            MFA_ENCRYPTION_PREVIOUS_KEYS={2: _PREVIOUS_MFA_ENCRYPTION_KEY},
        )

    with pytest.raises(ValidationError, match="distinct secrets"):
        _build(
            MFA_ENCRYPTION_KEY_VERSION=2,
            MFA_ENCRYPTION_PREVIOUS_KEYS={1: _MFA_ENCRYPTION_KEY},
        )


def test_development_allows_defaults() -> None:
    s = _build(
        ENVIRONMENT="development",
        JWT_SECRET="change-me-to-a-long-random-string-min-32-bytes",
        MINIO_ACCESS_KEY="minioadmin",
        MINIO_SECRET_KEY="minioadmin",
        DATABASE_URL_APP=_DEV_DB_APP,
        DATABASE_URL_SUPPORT=_DEV_DB_SUPPORT,
        REFRESH_COOKIE_SECURE=False,
    )
    assert s.ENVIRONMENT == "development"
    assert s.refresh_cookie_secure is False


def test_settings_read_secrets_from_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_values = {
        "DATABASE_URL_APP": _PROD_DB_APP,
        "DATABASE_URL_SUPPORT": _PROD_DB_SUPPORT,
        "JWT_SECRET": _STRONG_SECRET,
        "MFA_ENCRYPTION_KEY": _MFA_ENCRYPTION_KEY,
        "MFA_ENCRYPTION_PREVIOUS_KEYS": '{"1":"' + _PREVIOUS_MFA_ENCRYPTION_KEY + '"}',
        "METRICS_TOKEN": _METRICS_TOKEN,
        "REDIS_URL": "redis://:Str0ng-Redis-Pw@redis:6379/0",
        "MINIO_ACCESS_KEY": "application-user-key",
        "MINIO_SECRET_KEY": "m" * 40,
        "EMAIL_PASSWORD": "smtp-provider-token",
    }
    for name, value in secret_values.items():
        monkeypatch.delenv(name, raising=False)
        (tmp_path / name).write_text(value, encoding="utf-8")

    settings = Settings(
        _env_file=None,
        _secrets_dir=tmp_path,
        ENVIRONMENT="production",
        CORS_ORIGINS=[_PRODUCTION_ORIGIN],
        TRUSTED_HOSTS=["pharmacy.example.com"],
        TRUSTED_PROXY_IPS=["172.30.0.10"],
        REFRESH_COOKIE_SECURE=True,
        MFA_ENCRYPTION_KEY_VERSION=2,
        EMAIL_HOST="smtp.example.com",
        EMAIL_USER="aurum@example.com",
        EMAIL_FROM="no-reply@example.com",
    )

    assert settings.JWT_SECRET == _STRONG_SECRET
    assert settings.MFA_ENCRYPTION_KEY is not None
    assert settings.MFA_ENCRYPTION_KEY.get_secret_value() == _MFA_ENCRYPTION_KEY
    assert settings.MFA_ENCRYPTION_PREVIOUS_KEYS[1].get_secret_value() == (
        _PREVIOUS_MFA_ENCRYPTION_KEY
    )
