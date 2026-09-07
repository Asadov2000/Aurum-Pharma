"""Minimal broker-only configuration for Celery applications and beat."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import ClassVar, Literal
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import _redis_security_problems

BrokerRole = Literal["api", "beat", "system", "catalog", "billing", "mailer"]


class CeleryBrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    BROKER_ROLE: ClassVar[BrokerRole] = "api"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_api:aurum_api_broker_pw@redis:6379/0", repr=False
    )

    @model_validator(mode="after")
    def _guard_broker(self) -> CeleryBrokerSettings:
        problems: list[str] = []
        expected_user = f"aurum_{self.BROKER_ROLE}"
        try:
            parsed = urlsplit(self.CELERY_BROKER_URL)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            valid = (
                parsed.scheme in {"redis", "rediss"}
                and bool(parsed.hostname)
                and unquote(parsed.username or "") == expected_user
                and bool(parsed.password)
                and parsed.path == "/0"
                and "db" not in query
                and "username" not in query
                and "password" not in query
                and not parsed.fragment
            )
            placeholder = unquote(parsed.password or "").endswith("_broker_pw")
        except ValueError:
            valid = False
            placeholder = False
        if not valid:
            problems.append(
                f"CELERY_BROKER_URL must use {expected_user} credentials and database 0"
            )
        if self.ENVIRONMENT != "development":
            problems.extend(
                message.replace("REDIS_URL", "CELERY_BROKER_URL")
                for message in _redis_security_problems(self.CELERY_BROKER_URL)
            )
            if placeholder:
                problems.append("CELERY_BROKER_URL must use non-placeholder credentials")
        if problems:
            raise ValueError(
                "Refusing to start insecure Celery broker:\n- " + "\n- ".join(problems)
            )
        return self


class BeatBrokerSettings(CeleryBrokerSettings):
    BROKER_ROLE: ClassVar[BrokerRole] = "beat"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_beat:aurum_beat_broker_pw@redis:6379/0", repr=False
    )


class SystemBrokerSettings(CeleryBrokerSettings):
    BROKER_ROLE: ClassVar[BrokerRole] = "system"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_system:aurum_system_broker_pw@redis:6379/0", repr=False
    )


class CatalogBrokerSettings(CeleryBrokerSettings):
    BROKER_ROLE: ClassVar[BrokerRole] = "catalog"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_catalog:aurum_catalog_broker_pw@redis:6379/0", repr=False
    )


class BillingBrokerSettings(CeleryBrokerSettings):
    BROKER_ROLE: ClassVar[BrokerRole] = "billing"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_billing:aurum_billing_broker_pw@redis:6379/0", repr=False
    )


class MailerBrokerSettings(CeleryBrokerSettings):
    BROKER_ROLE: ClassVar[BrokerRole] = "mailer"
    CELERY_BROKER_URL: str = Field(
        default="redis://aurum_mailer:aurum_mailer_broker_pw@redis:6379/0", repr=False
    )


@lru_cache
def get_celery_broker_settings(role: BrokerRole = "api") -> CeleryBrokerSettings:
    settings_types: dict[BrokerRole, type[CeleryBrokerSettings]] = {
        "api": CeleryBrokerSettings,
        "beat": BeatBrokerSettings,
        "system": SystemBrokerSettings,
        "catalog": CatalogBrokerSettings,
        "billing": BillingBrokerSettings,
        "mailer": MailerBrokerSettings,
    }
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return settings_types[role](_secrets_dir=secrets_dir or None)
