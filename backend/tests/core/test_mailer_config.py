"""Security contract for the isolated invitation mailer configuration."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr, ValidationError

from app.core.mailer_config import MailerSettings
from app.core.security import derive_email_outbox_encryption_key

_ROOT = "e" * 40
_PREVIOUS_ROOT = "p" * 40


def _build(**overrides: object) -> MailerSettings:
    base: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL_MAILER": ("postgresql+asyncpg://aurum_mailer:Str0ng-Mailer-Pw@db/aurum"),
        "REDIS_URL": "redis://:Str0ng-Redis-Pw@redis:6379/0",
        "EMAIL_OUTBOX_ENCRYPTION_KEY": _ROOT,
        "EMAIL_HOST": "smtp.example.com",
        "EMAIL_USER": "mailer@example.com",
        "EMAIL_PASSWORD": "smtp-provider-token",
        "EMAIL_FROM": "no-reply@example.com",
        "EMAIL_USE_TLS": True,
        "PUBLIC_APP_URL": "https://pharmacy.example.com",
    }
    base.update(overrides)
    return MailerSettings(_env_file=None, **base)  # type: ignore[arg-type]


def test_production_accepts_only_dedicated_secure_configuration() -> None:
    settings = _build()
    representation = repr(settings)

    assert settings.ENVIRONMENT == "production"
    assert "Str0ng-Mailer-Pw" not in representation
    assert "Str0ng-Redis-Pw" not in representation
    assert "smtp-provider-token" not in representation
    assert _ROOT not in representation


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "DATABASE_URL_MAILER",
            "postgresql+asyncpg://aurum_support:x@db/aurum",
            "DATABASE_URL_MAILER",
        ),
        ("REDIS_URL", "redis://redis:6379/0", "REDIS_URL"),
        ("REDIS_URL", "redis://:[invalid", "REDIS_URL"),
        ("EMAIL_PASSWORD", "", "SMTP"),
        ("EMAIL_USE_TLS", False, "SMTP"),
        ("PUBLIC_APP_URL", "http://pharmacy.example.com", "PUBLIC_APP_URL"),
    ],
)
def test_production_rejects_insecure_configuration(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _build(**{field: value})


def test_mailer_keyring_matches_enqueue_key_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import security

    settings = _build(
        ENVIRONMENT="development",
        EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION=2,
        EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS={1: SecretStr(_PREVIOUS_ROOT)},
    )
    keyring = json.loads(settings.encryption_keyring_json())
    monkeypatch.setattr(security.settings, "EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION", 2)
    monkeypatch.setattr(
        security.settings,
        "EMAIL_OUTBOX_ENCRYPTION_KEY",
        SecretStr(_ROOT),
    )
    monkeypatch.setattr(
        security.settings,
        "EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS",
        {1: SecretStr(_PREVIOUS_ROOT)},
    )

    assert keyring["1"] == derive_email_outbox_encryption_key(version=1)
    assert keyring["2"] == derive_email_outbox_encryption_key(version=2)
