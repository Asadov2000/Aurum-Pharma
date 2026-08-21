"""Fail-closed configuration checks for the isolated billing worker."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.billing_worker_config import BillingWorkerSettings


def _production_settings(**overrides: object) -> BillingWorkerSettings:
    values: dict[str, object] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL_BILLING_WORKER": (
            "postgresql+asyncpg://aurum_billing_worker:Str0ng-Billing-Pw@db/aurum"
        ),
        "REDIS_URL": "rediss://:Str0ng-Redis-Pw@redis/0",
    }
    values.update(overrides)
    return BillingWorkerSettings(**values)


def test_production_accepts_only_dedicated_database_identity() -> None:
    settings = _production_settings()

    assert settings.BILLING_TRANSITION_BATCH_SIZE == 100
    assert "Str0ng-Billing-Pw" not in repr(settings)


@pytest.mark.parametrize(
    "database_url",
    (
        "postgresql+asyncpg://aurum_support:secret@db/aurum",
        "postgresql+asyncpg://aurum_billing_worker@db/aurum",
        "postgresql+asyncpg://aurum_billing_worker:aurum_billing_worker_pw@db/aurum",
    ),
)
def test_production_rejects_non_dedicated_database_credentials(database_url: str) -> None:
    with pytest.raises(ValidationError, match="dedicated aurum_billing_worker credentials"):
        _production_settings(DATABASE_URL_BILLING_WORKER=database_url)


def test_production_requires_authenticated_redis() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL must contain a host and password"):
        _production_settings(REDIS_URL="redis://redis:6379/0")


def test_batch_size_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _production_settings(BILLING_TRANSITION_BATCH_SIZE=101)


def test_validation_errors_do_not_expose_worker_secrets() -> None:
    database_secret = "worker-database-secret-must-stay-hidden"
    redis_secret = "worker-redis-secret-must-stay-hidden"

    with pytest.raises(ValidationError) as error:
        _production_settings(
            DATABASE_URL_BILLING_WORKER=(
                f"postgresql+asyncpg://aurum_support:{database_secret}@db/aurum"
            ),
            REDIS_URL=f"redis://:{redis_secret}@redis/0",
        )

    message = str(error.value)
    assert database_secret not in message
    assert redis_secret not in message


def test_billing_tasks_are_routed_only_to_dedicated_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "JWT_SECRET",
        "billing-routing-test-secret-at-least-32-characters",
    )

    from app.tasks.billing_app import billing_app
    from app.tasks.celery_app import celery_app

    expected_routes = {
        "billing.process_trial_endings": {"queue": "billing-worker"},
        "billing.process_grace_endings": {"queue": "billing-worker"},
    }

    assert billing_app.conf.task_default_queue == "billing-worker"
    assert billing_app.conf.task_ignore_result is True
    assert billing_app.conf.task_reject_on_worker_lost is True
    assert billing_app.conf.task_routes == expected_routes
    assert "app.tasks.billing" in billing_app.conf.include
    assert "app.tasks.billing" not in celery_app.conf.include
    for task_name, route in expected_routes.items():
        assert celery_app.conf.task_routes[task_name] == route


def test_billing_worker_import_needs_only_worker_configuration() -> None:
    env = os.environ.copy()
    for name in ("DATABASE_URL_APP", "DATABASE_URL_SUPPORT", "JWT_SECRET"):
        env.pop(name, None)
    env.update(
        {
            "ENVIRONMENT": "development",
            "DATABASE_URL_BILLING_WORKER": (
                "postgresql+asyncpg://aurum_billing_worker:worker-pw@db/aurum"
            ),
            "REDIS_URL": "redis://redis:6379/0",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", "import app.tasks.billing"],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
