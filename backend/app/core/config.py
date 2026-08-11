"""Application settings, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

import ipaddress
import logging
import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

logger = logging.getLogger("aurum.config")

# Example/dev values that must never reach production.
_DEFAULT_DB_PASSWORDS = ("aurum_app_pw", "aurum_support_pw", ":postgres@")


def _database_security_problems(name: str, url: str) -> list[str]:
    problems: list[str] = []
    if any(password in url for password in _DEFAULT_DB_PASSWORDS):
        problems.append(f"{name} uses a default/example DB password")
    try:
        parsed = make_url(url)
    except ArgumentError:
        problems.append(f"{name} is not a valid database URL")
        return problems

    if not parsed.username or not parsed.password or not parsed.host:
        problems.append(f"{name} must contain a host and dedicated credentials")
    return problems


def _cors_security_problems(origins: list[str]) -> tuple[list[str], set[str]]:
    if not origins:
        return (
            ["CORS_ORIGINS must contain the exact HTTPS application origin"],
            set(),
        )

    hosts: set[str] = set()
    for origin in origins:
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
        except ValueError:
            hostname = None
            parsed = None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            return (
                [
                    "CORS_ORIGINS must use exact public HTTPS origins "
                    "without paths or credentials"
                ],
                set(),
            )
        hosts.add(hostname.lower())
    return [], hosts


def _trusted_host_security_problems(
    configured_hosts: list[str],
    cors_hosts: set[str],
) -> list[str]:
    trusted_hosts = {host.lower() for host in configured_hosts}
    if not trusted_hosts or any("*" in host for host in trusted_hosts):
        return ["TRUSTED_HOSTS must contain exact hostnames without wildcards"]
    if not cors_hosts.issubset(trusted_hosts):
        return ["TRUSTED_HOSTS must include every CORS origin hostname"]
    return []


def _proxy_security_problems(proxy_ips: list[str]) -> list[str]:
    if not proxy_ips:
        return ["TRUSTED_PROXY_IPS must contain the reverse proxy address"]
    for value in proxy_ips:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            return ["TRUSTED_PROXY_IPS contains an invalid IP address or network"]
        if network.prefixlen == 0:
            return ["TRUSTED_PROXY_IPS must not trust every source"]
    return []


def _redis_security_problems(url: str) -> list[str]:
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"redis", "rediss"} and bool(parsed.hostname) and bool(parsed.password)
        )
    except ValueError:
        valid = False
    return [] if valid else ["REDIS_URL must contain a host and dedicated password"]


def _minio_security_problems(access_key: str, secret_key: str) -> list[str]:
    if "minioadmin" in (access_key, secret_key) or len(access_key) < 16 or len(secret_key) < 32:
        return ["MINIO_ACCESS_KEY/MINIO_SECRET_KEY must be strong, independent credentials"]
    return []


def _email_security_problems(
    host: str,
    user: str,
    password: str,
    sender: str,
    use_tls: bool,
) -> list[str]:
    problems: list[str] = []
    if not host or host.lower() == "localhost" or not user or not password or "@" not in sender:
        problems.append("Production SMTP settings must contain dedicated credentials")
    if not use_tls:
        problems.append("EMAIL_USE_TLS must be true outside development")
    return problems


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEPLOYMENT_PROFILE: Literal["cloud", "edge_shadow"] = "cloud"

    DATABASE_URL_APP: str = Field(repr=False)
    DATABASE_URL_SUPPORT: str = Field(repr=False)

    REDIS_URL: str = Field(default="redis://redis:6379/0", repr=False)

    JWT_SECRET: str = Field(repr=False)
    JWT_ALGORITHM: str = "HS256"
    MFA_ENCRYPTION_KEY: SecretStr | None = None
    MFA_ENCRYPTION_KEY_VERSION: int = Field(default=1, ge=1, le=32767)
    MFA_ENCRYPTION_PREVIOUS_KEYS: dict[int, SecretStr] = Field(default_factory=dict)
    EMAIL_OUTBOX_ENCRYPTION_KEY: SecretStr | None = None
    EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION: int = Field(default=1, ge=1, le=32767)
    EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS: dict[int, SecretStr] = Field(default_factory=dict)
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    MFA_STEP_UP_MINUTES: int = Field(default=10, ge=1, le=15)
    # Local account switching must not create long lockouts while developing.
    # Staging and production refuse to start with this flag enabled, so a
    # copied development configuration cannot weaken authentication.
    AUTH_LOCAL_TESTING_MODE: bool = False
    REFRESH_COOKIE_NAME: str = "aurum_refresh_token"
    REFRESH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    # None means "secure in production, HTTP-friendly in local development".
    REFRESH_COOKIE_SECURE: bool | None = None
    # Prometheus scrapes this endpoint from the private network. Staging and
    # production still require a separate bearer secret if the route is exposed.
    METRICS_TOKEN: SecretStr | None = None

    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    TRUSTED_HOSTS: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    TRUSTED_PROXY_IPS: list[str] = Field(default_factory=list)

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = Field(default="minioadmin", repr=False)
    MINIO_SECRET_KEY: str = Field(default="minioadmin", repr=False)
    MINIO_BUCKET: str = "aurum"
    MINIO_SECURE: bool = False

    # Upper bound for catalog import uploads (CSV/XLSX). Bigger files are
    # rejected at the upload endpoint with a friendly 422.
    MAX_IMPORT_FILE_MB: int = 10

    EMAIL_HOST: str = "localhost"
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASSWORD: str = Field(default="", repr=False)
    EMAIL_FROM: str = "no-reply@aurum-pharma.tj"
    EMAIL_USE_TLS: bool = True
    EMAIL_SMTP_TIMEOUT_SECONDS: int = Field(default=10, ge=3, le=30)
    EMAIL_OUTBOX_BATCH_SIZE: int = Field(default=25, ge=1, le=100)
    PUBLIC_APP_URL: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")

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

    @property
    def auth_login_guard_enabled(self) -> bool:
        return self.ENVIRONMENT != "development" or not self.AUTH_LOCAL_TESTING_MODE

    @model_validator(mode="after")
    def _guard_non_development_metrics(self) -> Settings:
        if self.ENVIRONMENT != "development" and (
            self.METRICS_TOKEN is None or len(self.METRICS_TOKEN.get_secret_value()) < 32
        ):
            raise ValueError("METRICS_TOKEN must be set to a strong secret (>=32 chars)")
        return self

    @model_validator(mode="after")
    def _guard_non_development_mfa_key(self) -> Settings:
        if self.ENVIRONMENT != "development" and self.MFA_ENCRYPTION_KEY is None:
            raise ValueError("MFA_ENCRYPTION_KEY must be set outside development")

        roots: dict[int, str] = {
            version: secret.get_secret_value()
            for version, secret in self.MFA_ENCRYPTION_PREVIOUS_KEYS.items()
        }
        if self.MFA_ENCRYPTION_KEY is not None:
            roots[self.MFA_ENCRYPTION_KEY_VERSION] = self.MFA_ENCRYPTION_KEY.get_secret_value()
        if self.MFA_ENCRYPTION_KEY_VERSION in self.MFA_ENCRYPTION_PREVIOUS_KEYS:
            raise ValueError("MFA_ENCRYPTION_PREVIOUS_KEYS must not contain the current version")
        for version, root in roots.items():
            if version < 1 or version > 32767:
                raise ValueError("MFA encryption key versions must be between 1 and 32767")
            if len(root) < 32:
                raise ValueError("MFA encryption keys must contain at least 32 characters")
            if root == self.JWT_SECRET:
                raise ValueError("MFA encryption keys must differ from JWT_SECRET")
        if len(set(roots.values())) != len(roots):
            raise ValueError("MFA encryption key versions must use distinct secrets")
        return self

    @model_validator(mode="after")
    def _guard_email_outbox_keyring(self) -> Settings:
        if self.ENVIRONMENT != "development" and self.EMAIL_OUTBOX_ENCRYPTION_KEY is None:
            raise ValueError("EMAIL_OUTBOX_ENCRYPTION_KEY must be set outside development")

        roots: dict[int, str] = {
            version: secret.get_secret_value()
            for version, secret in self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS.items()
        }
        if self.EMAIL_OUTBOX_ENCRYPTION_KEY is not None:
            roots[self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION] = (
                self.EMAIL_OUTBOX_ENCRYPTION_KEY.get_secret_value()
            )
        if self.EMAIL_OUTBOX_ENCRYPTION_KEY_VERSION in (self.EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS):
            raise ValueError(
                "EMAIL_OUTBOX_ENCRYPTION_PREVIOUS_KEYS must not contain the current version"
            )
        protected_roots = {self.JWT_SECRET}
        if self.MFA_ENCRYPTION_KEY is not None:
            protected_roots.add(self.MFA_ENCRYPTION_KEY.get_secret_value())
        protected_roots.update(
            secret.get_secret_value() for secret in self.MFA_ENCRYPTION_PREVIOUS_KEYS.values()
        )
        for version, root in roots.items():
            if version < 1 or version > 32767:
                raise ValueError("Email outbox key versions must be between 1 and 32767")
            if len(root) < 32:
                raise ValueError("Email outbox encryption keys must contain at least 32 characters")
            if root in protected_roots:
                raise ValueError("Email outbox encryption keys must be independent secrets")
        if len(set(roots.values())) != len(roots):
            raise ValueError("Email outbox encryption key versions must use distinct secrets")
        return self

    @model_validator(mode="after")
    def _guard_non_development_security(self) -> Settings:
        """Fail fast if a deployment would start with insecure configuration.
        Development keeps working with the defaults used in docker-compose and
        tests; staging uses the production-style operational gates."""
        if self.ENVIRONMENT == "development":
            return self

        problems: list[str] = []
        if self.AUTH_LOCAL_TESTING_MODE:
            problems.append("AUTH_LOCAL_TESTING_MODE is allowed only in development")
        if len(self.JWT_SECRET) < 32 or "change-me" in self.JWT_SECRET.lower():
            problems.append("JWT_SECRET must be a strong secret (>=32 chars, not the placeholder)")
        problems.extend(_minio_security_problems(self.MINIO_ACCESS_KEY, self.MINIO_SECRET_KEY))
        for name, url in (
            ("DATABASE_URL_APP", self.DATABASE_URL_APP),
            ("DATABASE_URL_SUPPORT", self.DATABASE_URL_SUPPORT),
        ):
            problems.extend(_database_security_problems(name, url))

        if not self.refresh_cookie_secure:
            problems.append("REFRESH_COOKIE_SECURE must be true outside development")

        cors_problems, cors_hosts = _cors_security_problems(self.CORS_ORIGINS)
        problems.extend(cors_problems)
        problems.extend(_trusted_host_security_problems(self.TRUSTED_HOSTS, cors_hosts))
        problems.extend(_proxy_security_problems(self.TRUSTED_PROXY_IPS))
        problems.extend(_redis_security_problems(self.REDIS_URL))
        problems.extend(
            _email_security_problems(
                self.EMAIL_HOST,
                self.EMAIL_USER,
                self.EMAIL_PASSWORD,
                self.EMAIL_FROM,
                self.EMAIL_USE_TLS,
            )
        )
        public_app_url = urlsplit(str(self.PUBLIC_APP_URL))
        if (
            public_app_url.scheme != "https"
            or public_app_url.username is not None
            or public_app_url.password is not None
            or public_app_url.query
            or public_app_url.fragment
        ):
            problems.append("PUBLIC_APP_URL must be a public HTTPS URL without credentials")

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

        # Non-fatal for the first single-host perimeter: object-storage TLS is
        # still a release blocker and remains tracked in the security plan.
        if not self.MINIO_SECURE:
            logger.warning(
                "MINIO_SECURE=false outside development - object storage traffic is unencrypted"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return Settings(_secrets_dir=secrets_dir or None)
