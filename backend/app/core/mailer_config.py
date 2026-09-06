"""Minimal configuration owned exclusively by the invitation mailer process."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import _database_security_problems, _redis_security_problems


class MailerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DATABASE_URL_MAILER: str = Field(
        default="postgresql+asyncpg://aurum_mailer:aurum_mailer_pw@postgres:5432/aurum",
        repr=False,
    )
    REDIS_URL: str = Field(default="redis://redis:6379/0", repr=False)
    EMAIL_OUTBOX_ENCRYPTION_KEY: SecretStr | None = None
    EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION: int = Field(default=1, ge=1, le=32767)
    EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS: dict[int, SecretStr] = Field(default_factory=dict)
    EMAIL_HOST: str = "localhost"
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASSWORD: SecretStr = Field(default=SecretStr(""), repr=False)
    EMAIL_FROM: str = "no-reply@aurum-pharma.tj"
    EMAIL_USE_TLS: bool = True
    EMAIL_SMTP_TIMEOUT_SECONDS: int = Field(default=10, ge=3, le=30)
    EMAIL_OUTBOX_BATCH_SIZE: int = Field(default=25, ge=1, le=100)
    EMAIL_OUTBOX_CLAIM_TIMEOUT_SECONDS: int = Field(default=300, ge=60, le=1800)
    PUBLIC_APP_URL: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")
    LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def _guard_mailer(self) -> MailerSettings:
        self._validate_keyring()
        if self.ENVIRONMENT == "development":
            return self
        problems = self._production_security_problems()
        if problems:
            raise ValueError("Refusing to start insecure mailer:\n- " + "\n- ".join(problems))
        return self

    def _validate_keyring(self) -> None:
        roots = {
            version: secret.get_secret_value()
            for version, secret in self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS.items()
        }
        if self.EMAIL_OUTBOX_ENCRYPTION_KEY is not None:
            roots[self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION] = (
                self.EMAIL_OUTBOX_ENCRYPTION_KEY.get_secret_value()
            )
        if self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION in self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS:
            raise ValueError("Previous email keys must not contain the current version")
        if any(len(secret) < 32 for secret in roots.values()):
            raise ValueError("Email outbox encryption keys must contain at least 32 characters")
        if len(set(roots.values())) != len(roots):
            raise ValueError("Email outbox encryption key versions must use distinct secrets")

    def _production_security_problems(self) -> list[str]:
        problems: list[str] = []
        try:
            database = make_url(self.DATABASE_URL_MAILER)
        except ArgumentError:
            database = None
        if (
            database is None
            or database.username != "aurum_mailer"
            or not database.password
            or not database.host
            or "aurum_mailer_pw" in self.DATABASE_URL_MAILER
        ):
            problems.append("DATABASE_URL_MAILER must use dedicated aurum_mailer credentials")
        problems.extend(
            _database_security_problems("DATABASE_URL_MAILER", self.DATABASE_URL_MAILER)
        )
        problems.extend(_redis_security_problems(self.REDIS_URL))
        if self.EMAIL_OUTBOX_ENCRYPTION_KEY is None:
            problems.append("EMAIL_OUTBOX_ENCRYPTION_KEY is required")
        if (
            not self.EMAIL_HOST
            or self.EMAIL_HOST.lower() == "localhost"
            or not self.EMAIL_USER
            or not self.EMAIL_PASSWORD.get_secret_value()
            or "@" not in self.EMAIL_FROM
            or not self.EMAIL_USE_TLS
        ):
            problems.append("Dedicated SMTP credentials with TLS are required")
        public_url = urlsplit(str(self.PUBLIC_APP_URL))
        if (
            public_url.scheme != "https"
            or public_url.username is not None
            or public_url.password is not None
            or public_url.query
            or public_url.fragment
        ):
            problems.append("PUBLIC_APP_URL must be a public HTTPS URL without credentials")
        return problems

    def encryption_keyring_json(self) -> str:
        versions = set(self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS)
        versions.add(self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION)
        keyring = {
            str(version): self._derive_encryption_key(version) for version in sorted(versions)
        }
        return json.dumps(keyring, separators=(",", ":"), sort_keys=True)

    def _derive_encryption_key(self, version: int) -> str:
        if (
            version == self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION
            and self.EMAIL_OUTBOX_ENCRYPTION_KEY is not None
        ):
            root = self.EMAIL_OUTBOX_ENCRYPTION_KEY.get_secret_value().encode()
        elif version in self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS:
            root = self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS[version].get_secret_value().encode()
        elif self.ENVIRONMENT == "development" and version == 1:
            root = hmac.new(
                b"development-mailer-only",
                b"aurum-email-outbox-root:v1",
                hashlib.sha256,
            ).digest()
        else:
            raise ValueError(f"Email outbox encryption key version {version} is unavailable")
        return hmac.new(
            root,
            f"aurum-email-outbox-pgcrypto:v{version}".encode(),
            hashlib.sha256,
        ).hexdigest()


@lru_cache
def get_mailer_settings() -> MailerSettings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return MailerSettings(_secrets_dir=secrets_dir or None)
