"""Transport TLS contracts for isolated worker configurations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.catalog_worker_config import CatalogWorkerSettings
from app.core.system_worker_config import SystemWorkerSettings

_POSTGRES_TLS = "?sslmode=verify-full&sslrootcert=/run/secrets/postgres_ca.crt"
_REDIS_TLS_URL = (
    "rediss://aurum_catalog:Str0ng-Redis-Pw@redis:6379/0"
    "?ssl_cert_reqs=required&ssl_check_hostname=true"
)


def _catalog_settings(**overrides: object) -> CatalogWorkerSettings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL_CATALOG_WORKER": (
            "postgresql+asyncpg://aurum_catalog_worker:Str0ng-App-Pw@db:5432/aurum" + _POSTGRES_TLS
        ),
        "CELERY_BROKER_URL": _REDIS_TLS_URL,
        "MINIO_ENDPOINT": "minio:9000",
        "CATALOG_MINIO_ACCESS_KEY": "catalog-access-key",
        "CATALOG_MINIO_SECRET_KEY": "catalog-secret-key-with-at-least-32-characters",
        "MINIO_BUCKET": "aurum",
        "MINIO_SECURE": True,
    }
    values.update(overrides)
    return CatalogWorkerSettings(**values)


def _system_settings(**overrides: object) -> SystemWorkerSettings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL_WORKER": (
            "postgresql+asyncpg://aurum_worker:Str0ng-Worker-Pw@db:5432/aurum" + _POSTGRES_TLS
        ),
        "CELERY_BROKER_URL": _REDIS_TLS_URL.replace("aurum_catalog", "aurum_system"),
    }
    values.update(overrides)
    return SystemWorkerSettings(**values)


def test_production_workers_accept_verified_internal_transports() -> None:
    assert _catalog_settings().MINIO_SECURE is True
    assert _system_settings().ENVIRONMENT == "production"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://aurum_catalog_worker:Str0ng-App-Pw@db:5432/aurum",
        (
            "postgresql+asyncpg://aurum_catalog_worker:Str0ng-App-Pw@db:5432/aurum"
            "?sslmode=verify-full"
        ),
    ],
)
def test_catalog_worker_rejects_database_without_verified_tls(database_url: str) -> None:
    with pytest.raises(ValidationError, match="sslmode=verify-full and sslrootcert"):
        _catalog_settings(DATABASE_URL_CATALOG_WORKER=database_url)


def test_catalog_worker_rejects_redis_without_verified_tls() -> None:
    with pytest.raises(ValidationError, match="ssl_cert_reqs=required"):
        _catalog_settings(CELERY_BROKER_URL="redis://aurum_catalog:Str0ng-Redis-Pw@redis:6379/0")


def test_catalog_worker_rejects_minio_without_https() -> None:
    with pytest.raises(ValidationError, match="MINIO_SECURE"):
        _catalog_settings(MINIO_SECURE=False)


def test_system_worker_rejects_database_without_verified_tls() -> None:
    with pytest.raises(ValidationError, match="sslmode=verify-full and sslrootcert"):
        _system_settings(
            DATABASE_URL_WORKER=("postgresql+asyncpg://aurum_worker:Str0ng-Worker-Pw@db:5432/aurum")
        )


def test_system_worker_rejects_redis_without_verified_tls() -> None:
    with pytest.raises(ValidationError, match="ssl_cert_reqs=required"):
        _system_settings(CELERY_BROKER_URL="rediss://aurum_system:Str0ng-Redis-Pw@redis:6379/0")


def test_development_workers_keep_local_transport_defaults() -> None:
    catalog = CatalogWorkerSettings(ENVIRONMENT="development")
    system = SystemWorkerSettings(ENVIRONMENT="development")

    assert catalog.MINIO_SECURE is False
    assert catalog.CELERY_BROKER_URL.startswith("redis://")
    assert system.DATABASE_URL_WORKER.startswith("postgresql+asyncpg://")
