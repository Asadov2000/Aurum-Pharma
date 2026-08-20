"""Minimal configuration owned exclusively by the billing worker process."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class BillingWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DATABASE_URL_BILLING_WORKER: str = Field(
        default=(
            "postgresql+asyncpg://aurum_billing_worker:aurum_billing_worker_pw@"
            "postgres:5432/aurum"
        ),
        repr=False,
    )
    REDIS_URL: str = Field(default="redis://redis:6379/0", repr=False)
    BILLING_TRANSITION_BATCH_SIZE: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def _guard_worker(self) -> BillingWorkerSettings:
        if self.ENVIRONMENT == "development":
            return self

        problems: list[str] = []
        try:
            database = make_url(self.DATABASE_URL_BILLING_WORKER)
        except ArgumentError:
            database = None
        if (
            database is None
            or database.username != "aurum_billing_worker"
            or not database.password
            or not database.host
            or "aurum_billing_worker_pw" in self.DATABASE_URL_BILLING_WORKER
        ):
            problems.append(
                "DATABASE_URL_BILLING_WORKER must use dedicated " "aurum_billing_worker credentials"
            )

        try:
            redis = urlsplit(self.REDIS_URL)
            redis_is_secure = (
                redis.scheme in {"redis", "rediss"}
                and bool(redis.hostname)
                and bool(redis.password)
            )
        except ValueError:
            redis_is_secure = False
        if not redis_is_secure:
            problems.append("REDIS_URL must contain a host and password")

        if problems:
            raise ValueError(
                "Refusing to start insecure billing worker:\n- " + "\n- ".join(problems)
            )
        return self


@lru_cache
def get_billing_worker_settings() -> BillingWorkerSettings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return BillingWorkerSettings(_secrets_dir=secrets_dir or None)
