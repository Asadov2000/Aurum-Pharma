"""Minimal configuration for cross-tenant maintenance tasks."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.celery_broker_config import CeleryBrokerSettings


class SystemWorkerSettings(CeleryBrokerSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DATABASE_URL_SUPPORT: str = Field(
        default="postgresql+asyncpg://aurum_support:aurum_support_pw@postgres:5432/aurum",
        repr=False,
    )

    @model_validator(mode="after")
    def _guard_system_worker(self) -> SystemWorkerSettings:
        if self.ENVIRONMENT == "development":
            return self
        try:
            database = make_url(self.DATABASE_URL_SUPPORT)
        except ArgumentError:
            database = None
        if (
            database is None
            or database.username != "aurum_support"
            or not database.password
            or not database.host
            or "aurum_support_pw" in self.DATABASE_URL_SUPPORT
        ):
            raise ValueError(
                "DATABASE_URL_SUPPORT must use non-placeholder aurum_support credentials"
            )
        return self


@lru_cache
def get_system_worker_settings() -> SystemWorkerSettings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return SystemWorkerSettings(_secrets_dir=secrets_dir or None)
