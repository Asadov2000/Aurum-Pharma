"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("aurum.config")

# Example/dev values that must never reach production.
_DEFAULT_DB_PASSWORDS = ("aurum_app_pw", "aurum_support_pw", ":postgres@")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEPLOYMENT_PROFILE: Literal["cloud", "edge_shadow"] = "cloud"

    DATABASE_URL_APP: str
    DATABASE_URL_SUPPORT: str

    REDIS_URL: str = "redis://redis:6379/0"

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    REFRESH_COOKIE_NAME: str = "aurum_refresh_token"
    REFRESH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    # None means "secure in production, HTTP-friendly in local development".
    REFRESH_COOKIE_SECURE: bool | None = None
    # Prometheus scrapes this endpoint from the private network. Staging and
    # production still require a separate bearer secret if the route is exposed.
    METRICS_TOKEN: SecretStr | None = None

    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "aurum"
    MINIO_SECURE: bool = False

    # Upper bound for catalog import uploads (CSV/XLSX). Bigger files are
    # rejected at the upload endpoint with a friendly 422.
    MAX_IMPORT_FILE_MB: int = 10

    EMAIL_HOST: str = "localhost"
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASSWORD: str = ""
    EMAIL_FROM: str = "no-reply@aurum-pharma.tj"
    EMAIL_USE_TLS: bool = True

    APP_NAME: str = "Aurum Pharma"
    LOG_LEVEL: str = "INFO"

    # Development-only shadow transport. Production enrollment will use mTLS
    # and hardware-backed device identities; the bearer credential is refused
    # by the production configuration guard.
    EDGE_SYNC_ENABLED: bool = False
    EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS: int = Field(default=300, ge=30, le=900)
    EDGE_SYNC_NONCE_TTL_SECONDS: int = Field(default=600, ge=60, le=3600)
    EDGE_SYNC_REQUESTS_PER_MINUTE: int = Field(default=120, ge=10, le=1000)
    EDGE_SYNC_CLOUD_URL: AnyHttpUrl | None = None
    EDGE_SYNC_CREDENTIAL: SecretStr | None = None
    EDGE_SYNC_POLL_SECONDS: int = Field(default=5, ge=1, le=300)
    EDGE_SYNC_BATCH_SIZE: int = Field(default=100, ge=1, le=100)
    EDGE_BOOTSTRAP_TTL_SECONDS: int = Field(default=86400, ge=300, le=604800)
    EDGE_BOOTSTRAP_CHUNK_SIZE: int = Field(default=100, ge=1, le=100)
    EDGE_BOOTSTRAP_MAX_EVENTS: int = Field(default=10000, ge=100, le=100000)
    # Fail-closed until mTLS, signed bootstrap, and the cash-only Edge runtime
    # are all available. The handover protocol can still be exercised in tests.
    EDGE_WRITER_READINESS_ENABLED: bool = False
    EDGE_WRITER_ACTIVATION_ENABLED: bool = False

    @property
    def refresh_cookie_secure(self) -> bool:
        return (
            self.REFRESH_COOKIE_SECURE
            if self.REFRESH_COOKIE_SECURE is not None
            else self.ENVIRONMENT == "production"
        )

    @model_validator(mode="after")
    def _guard_non_development_metrics(self) -> Settings:
        if self.ENVIRONMENT != "development" and (
            self.METRICS_TOKEN is None or len(self.METRICS_TOKEN.get_secret_value()) < 32
        ):
            raise ValueError("METRICS_TOKEN must be set to a strong secret (>=32 chars)")
        return self

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        """Fail fast if a deployment would start with insecure configuration.
        Development keeps working with the defaults used in docker-compose and
        tests; staging uses the production-style operational gates."""
        if self.ENVIRONMENT != "production":
            return self

        problems: list[str] = []
        if len(self.JWT_SECRET) < 32 or "change-me" in self.JWT_SECRET.lower():
            problems.append("JWT_SECRET must be a strong secret (≥32 chars, not the placeholder)")
        if "minioadmin" in (self.MINIO_ACCESS_KEY, self.MINIO_SECRET_KEY):
            problems.append(
                "MINIO_ACCESS_KEY/MINIO_SECRET_KEY must not be the default 'minioadmin'"
            )
        for name, url in (
            ("DATABASE_URL_APP", self.DATABASE_URL_APP),
            ("DATABASE_URL_SUPPORT", self.DATABASE_URL_SUPPORT),
        ):
            if any(p in url for p in _DEFAULT_DB_PASSWORDS):
                problems.append(f"{name} uses a default/example DB password")
        if self.REFRESH_COOKIE_SAMESITE == "none" and not self.refresh_cookie_secure:
            problems.append("REFRESH_COOKIE_SAMESITE=none requires REFRESH_COOKIE_SECURE=true")
        if self.EDGE_SYNC_ENABLED:
            problems.append(
                "EDGE_SYNC_ENABLED uses development token auth; production requires mTLS"
            )
        if self.EDGE_SYNC_CREDENTIAL is not None:
            problems.append(
                "EDGE_SYNC_CREDENTIAL uses development token auth; production requires mTLS"
            )
        if self.EDGE_WRITER_ACTIVATION_ENABLED:
            problems.append(
                "EDGE_WRITER_ACTIVATION_ENABLED requires the production Edge security stack"
            )
        if self.EDGE_WRITER_READINESS_ENABLED:
            problems.append(
                "EDGE_WRITER_READINESS_ENABLED requires a complete production Edge bootstrap"
            )
        if problems:
            raise ValueError(
                "Refusing to start in production with insecure config:\n- " + "\n- ".join(problems)
            )

        # Non-fatal: object-storage traffic should be encrypted in production.
        if not self.MINIO_SECURE:
            logger.warning(
                "MINIO_SECURE=false in production — object storage traffic is unencrypted"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
