"""Fail-closed configuration and routing contracts for Celery processes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.catalog_worker_config import CatalogWorkerSettings
from app.core.catalog_worker_storage import validate_catalog_import_path
from app.core.celery_broker_config import CeleryBrokerSettings
from app.core.system_worker_config import SystemWorkerSettings

_REDIS_URL = "rediss://:Str0ng-Redis-Pw@redis:6379/0"


def test_beat_configuration_requires_only_authenticated_redis() -> None:
    settings = CeleryBrokerSettings(ENVIRONMENT="production", REDIS_URL=_REDIS_URL)

    assert settings.ENVIRONMENT == "production"
    assert "Str0ng-Redis-Pw" not in repr(settings)


def test_beat_configuration_rejects_unauthenticated_redis() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        CeleryBrokerSettings(
            ENVIRONMENT="production",
            REDIS_URL="redis://redis:6379/0",
        )


def test_system_worker_accepts_only_support_identity() -> None:
    settings = SystemWorkerSettings(
        ENVIRONMENT="production",
        DATABASE_URL_SUPPORT=("postgresql+asyncpg://aurum_support:Str0ng-Support-Pw@db:5432/aurum"),
        REDIS_URL=_REDIS_URL,
    )

    assert "Str0ng-Support-Pw" not in repr(settings)


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql+asyncpg://aurum_app:secret@db:5432/aurum",
        "postgresql+asyncpg://aurum_support@db:5432/aurum",
        "postgresql+asyncpg://aurum_support:aurum_support_pw@db:5432/aurum",
    ),
)
def test_system_worker_rejects_wrong_database_identity(database_url: str) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL_SUPPORT"):
        SystemWorkerSettings(
            ENVIRONMENT="production",
            DATABASE_URL_SUPPORT=database_url,
            REDIS_URL=_REDIS_URL,
        )


def _catalog_settings(**overrides: object) -> CatalogWorkerSettings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL_APP": ("postgresql+asyncpg://aurum_app:Str0ng-App-Pw@db:5432/aurum"),
        "REDIS_URL": _REDIS_URL,
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ACCESS_KEY": "catalog-access-key",
        "MINIO_SECRET_KEY": "catalog-secret-key-with-at-least-32-characters",
        "MINIO_BUCKET": "aurum",
    }
    values.update(overrides)
    return CatalogWorkerSettings(**values)


def test_catalog_worker_accepts_only_tenant_scoped_dependencies() -> None:
    settings = _catalog_settings()

    representation = repr(settings)
    assert "Str0ng-App-Pw" not in representation
    assert "catalog-secret-key" not in representation


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        (
            "DATABASE_URL_APP",
            "postgresql+asyncpg://aurum_support:secret@db:5432/aurum",
            "DATABASE_URL_APP",
        ),
        ("MINIO_ACCESS_KEY", "minioadmin", "MinIO"),
        ("MINIO_SECRET_KEY", "short", "MinIO"),
    ),
)
def test_catalog_worker_rejects_privileged_or_default_credentials(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _catalog_settings(**{field: value})


def test_catalog_storage_accepts_only_matching_tenant_import_path() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"

    assert validate_catalog_import_path(
        f"aurum/{tenant_id}/imports/catalog.csv",
        tenant_id=tenant_id,
        expected_bucket="aurum",
    ) == ("aurum", f"{tenant_id}/imports/catalog.csv")


@pytest.mark.parametrize(
    "object_path",
    (
        "other/11111111-1111-1111-1111-111111111111/imports/catalog.csv",
        "aurum/22222222-2222-2222-2222-222222222222/imports/catalog.csv",
        "aurum/11111111-1111-1111-1111-111111111111/images/catalog.csv",
        "aurum/11111111-1111-1111-1111-111111111111/imports/../receipt.pdf",
        "aurum/11111111-1111-1111-1111-111111111111/imports/",
    ),
)
def test_catalog_storage_rejects_cross_tenant_or_non_import_path(
    object_path: str,
) -> None:
    with pytest.raises(ValueError, match="tenant import boundary"):
        validate_catalog_import_path(
            object_path,
            tenant_id="11111111-1111-1111-1111-111111111111",
            expected_bucket="aurum",
        )


def test_catalog_tasks_are_routed_only_to_dedicated_queue() -> None:
    from app.tasks.catalog_app import catalog_app
    from app.tasks.celery_app import celery_app

    expected_route = {"catalog.import_catalog_job": {"queue": "catalog-worker"}}
    assert catalog_app.conf.task_default_queue == "catalog-worker"
    assert catalog_app.conf.task_routes == expected_route
    assert catalog_app.conf.task_ignore_result is True
    assert catalog_app.conf.task_reject_on_worker_lost is True
    assert "app.tasks.catalog" in catalog_app.conf.include
    assert "app.tasks.catalog" not in celery_app.conf.include
    assert celery_app.conf.task_routes["catalog.import_catalog_job"] == {"queue": "catalog-worker"}


@pytest.mark.parametrize(
    ("module", "extra_environment"),
    (
        ("app.tasks.celery_app", {}),
        (
            "app.tasks.auth",
            {
                "DATABASE_URL_SUPPORT": (
                    "postgresql+asyncpg://aurum_support:worker-pw@db:5432/aurum"
                )
            },
        ),
        (
            "app.tasks.foundation",
            {
                "DATABASE_URL_SUPPORT": (
                    "postgresql+asyncpg://aurum_support:worker-pw@db:5432/aurum"
                )
            },
        ),
        (
            "app.tasks.notifications",
            {
                "DATABASE_URL_SUPPORT": (
                    "postgresql+asyncpg://aurum_support:worker-pw@db:5432/aurum"
                )
            },
        ),
        (
            "app.tasks.catalog",
            {
                "DATABASE_URL_APP": ("postgresql+asyncpg://aurum_app:worker-pw@db:5432/aurum"),
                "MINIO_ENDPOINT": "minio:9000",
                "MINIO_ACCESS_KEY": "minioadmin",
                "MINIO_SECRET_KEY": "minioadmin",
                "MINIO_BUCKET": "aurum",
            },
        ),
    ),
)
def test_worker_modules_import_without_unrelated_application_secrets(
    module: str,
    extra_environment: dict[str, str],
) -> None:
    env = os.environ.copy()
    for name in (
        "DATABASE_URL_APP",
        "DATABASE_URL_SUPPORT",
        "JWT_SECRET",
        "MFA_ENCRYPTION_KEY",
        "EMAIL_OUTBOX_ENCRYPTION_KEY",
        "METRICS_TOKEN",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
    ):
        env.pop(name, None)
    env.update(
        {
            "ENVIRONMENT": "development",
            "REDIS_URL": "redis://redis:6379/0",
            **extra_environment,
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
