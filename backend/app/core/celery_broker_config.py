"""Minimal broker-only configuration for Celery applications and beat."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import _redis_security_problems


class CeleryBrokerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    REDIS_URL: str = Field(default="redis://redis:6379/0", repr=False)

    @model_validator(mode="after")
    def _guard_broker(self) -> CeleryBrokerSettings:
        if self.ENVIRONMENT == "development":
            return self
        problems = _redis_security_problems(self.REDIS_URL)
        if problems:
            raise ValueError(
                "Refusing to start insecure Celery broker:\n- " + "\n- ".join(problems)
            )
        return self


@lru_cache
def get_celery_broker_settings() -> CeleryBrokerSettings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return CeleryBrokerSettings(_secrets_dir=secrets_dir or None)
