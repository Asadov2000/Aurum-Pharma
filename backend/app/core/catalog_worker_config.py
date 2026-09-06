"""Minimal tenant-scoped configuration for catalog import workers."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.celery_broker_config import CeleryBrokerSettings
from app.core.config import (
    _database_security_problems,
    _minio_transport_security_problems,
    _redis_security_problems,
)


class CatalogWorkerSettings(CeleryBrokerSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DATABASE_URL_APP: str = Field(
        default="postgresql+asyncpg://aurum_app:aurum_app_pw@postgres:5432/aurum",
        repr=False,
    )
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = Field(default="minioadmin", repr=False)
    MINIO_SECRET_KEY: str = Field(default="minioadmin", repr=False)
    MINIO_BUCKET: str = "aurum"
    MINIO_SECURE: bool = False

    @model_validator(mode="after")
    def _guard_catalog_worker(self) -> CatalogWorkerSettings:
        if self.ENVIRONMENT == "development":
            return self
        problems: list[str] = []
        try:
            database = make_url(self.DATABASE_URL_APP)
        except ArgumentError:
            database = None
        if (
            database is None
            or database.username != "aurum_app"
            or not database.password
            or not database.host
            or "aurum_app_pw" in self.DATABASE_URL_APP
        ):
            problems.append("DATABASE_URL_APP must use non-placeholder aurum_app credentials")
        problems.extend(_database_security_problems("DATABASE_URL_APP", self.DATABASE_URL_APP))
        problems.extend(_redis_security_problems(self.REDIS_URL))
        if (
            not self.MINIO_ENDPOINT
            or "://" in self.MINIO_ENDPOINT
            or self.MINIO_ACCESS_KEY.lower() == "minioadmin"
            or len(self.MINIO_ACCESS_KEY) < 16
            or self.MINIO_SECRET_KEY.lower() == "minioadmin"
            or len(self.MINIO_SECRET_KEY) < 32
            or not self.MINIO_BUCKET
        ):
            problems.append("dedicated MinIO endpoint, bucket and credentials are required")
        problems.extend(_minio_transport_security_problems(self.MINIO_SECURE))
        if problems:
            raise ValueError(
                "Refusing to start insecure catalog worker:\n- " + "\n- ".join(problems)
            )
        return self


@lru_cache
def get_catalog_worker_settings() -> CatalogWorkerSettings:
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    return CatalogWorkerSettings(_secrets_dir=secrets_dir or None)
