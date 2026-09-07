"""Producer imports, broker identity, queue routing and local health contracts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from celery import Celery
from pydantic import ValidationError

from app.core.celery_broker_config import (
    BeatBrokerSettings,
    BillingBrokerSettings,
    CatalogBrokerSettings,
    CeleryBrokerSettings,
    MailerBrokerSettings,
    SystemBrokerSettings,
)
from app.tasks.broker_contract import configure_task_app

_SETTINGS_TYPES = (
    CeleryBrokerSettings,
    BeatBrokerSettings,
    SystemBrokerSettings,
    CatalogBrokerSettings,
    BillingBrokerSettings,
    MailerBrokerSettings,
)
_TLS = "?ssl_cert_reqs=required&ssl_check_hostname=true"


@pytest.mark.parametrize("settings_type", _SETTINGS_TYPES)
def test_broker_requires_its_own_identity(settings_type: type[CeleryBrokerSettings]) -> None:
    role = settings_type.BROKER_ROLE
    url = f"rediss://aurum_{role}:dedicated-test-secret@redis:6379/0{_TLS}"
    settings = settings_type(ENVIRONMENT="production", CELERY_BROKER_URL=url)
    assert settings.CELERY_BROKER_URL == url
    assert "dedicated-test-secret" not in repr(settings)
    with pytest.raises(ValidationError, match=f"aurum_{role}"):
        settings_type(
            ENVIRONMENT="production",
            CELERY_BROKER_URL=url.replace(f"aurum_{role}:", "foreign_role:"),
        )


@pytest.mark.parametrize(
    "suffix", ["/1", "/0?db=1", "/0?username=default", "/0?password=other", "/0#fragment"]
)
def test_broker_rejects_connection_overrides(suffix: str) -> None:
    with pytest.raises(ValidationError, match="CELERY_BROKER_URL"):
        CeleryBrokerSettings(CELERY_BROKER_URL=f"redis://aurum_api:secret@redis:6379{suffix}")


@pytest.mark.parametrize("settings_type", _SETTINGS_TYPES)
def test_production_broker_rejects_development_password(
    settings_type: type[CeleryBrokerSettings],
) -> None:
    role = settings_type.BROKER_ROLE
    with pytest.raises(ValidationError, match="non-placeholder"):
        settings_type(
            ENVIRONMENT="production",
            CELERY_BROKER_URL=f"rediss://aurum_{role}:aurum_{role}_broker_pw@redis:6379/0{_TLS}",
        )


@pytest.mark.parametrize(
    "queue", ["default", "catalog-worker", "billing-worker", "platform-mailer"]
)
def test_worker_has_only_its_queue_and_recovery_namespace(queue: str) -> None:
    app = Celery("isolated-test", broker="memory://")
    configure_task_app(app, queues=(queue,), worker_queue=queue)
    assert list(app.amqp.queues) == [queue]
    assert app.amqp.queues[queue].exchange.name == queue
    assert app.conf.broker_transport_options == {
        "unacked_key": f"aurum:broker:{queue}:unacked",
        "unacked_index_key": f"aurum:broker:{queue}:unacked_index",
        "unacked_mutex_key": f"aurum:broker:{queue}:unacked_mutex",
    }
    assert app.conf.task_ignore_result is True
    assert app.conf.result_backend is None
    assert app.conf.worker_enable_remote_control is False
    assert app.conf.worker_send_task_events is False
    assert app.conf.task_send_sent_event is False
    with pytest.raises(KeyError):
        app.amqp.router.route({"queue": "foreign-queue"}, "unknown.task")


@pytest.mark.parametrize("role", ["api", "beat"])
def test_production_producer_imports_without_worker_credentials(role: str) -> None:
    # A fresh interpreter proves worker modules cannot be hidden by pytest's import cache.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DATABASE_URL", "MINIO", "CATALOG_MINIO", "EMAIL_"))
        and key not in {"JWT_SECRET", "MFA_ENCRYPTION_KEY", "AURUM_SECRETS_DIR"}
    }
    env.update(
        ENVIRONMENT="production",
        CELERY_BROKER_URL=f"rediss://aurum_{role}:dedicated-test-secret@redis:6379/0{_TLS}",
    )
    script = """
import sys
from unittest.mock import patch
from uuid import uuid4
if sys.argv[1] == 'api':
    from app.tasks.catalog_producer import enqueue_catalog_job, producer_app
    ids = (uuid4(), uuid4(), uuid4())
    with patch.object(producer_app, 'send_task') as send:
        enqueue_catalog_job('preview', *ids)
        send.assert_called_once_with('catalog.preview_catalog_job',
            args=tuple(str(value) for value in ids), ignore_result=True)
else:
    from app.tasks.beat_app import beat_app
    assert not beat_app.conf.include
for module in sys.modules:
    assert module not in {'app.tasks.catalog', 'app.tasks.billing', 'app.tasks.auth_mailer',
        'app.core.catalog_worker_db', 'app.core.catalog_worker_storage',
        'app.core.system_worker_db', 'app.core.mailer_db'}
"""
    result = subprocess.run(
        [sys.executable, "-c", script, role],
        cwd=Path(__file__).parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_worker_health_checks_pid_and_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.tasks import worker_health

    pidfile = tmp_path / "worker.pid"
    pidfile.write_text("1\n", encoding="utf-8")
    kill = Mock()
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.ping.return_value = True
    connection = Mock(return_value=client)
    monkeypatch.setattr(worker_health.os, "kill", kill)
    monkeypatch.setattr(worker_health.redis.Redis, "from_url", connection)
    worker_health.check_worker_health("catalog", pidfile)
    kill.assert_called_once_with(1, 0)
    client.ping.assert_called_once_with()


def test_worker_health_rejects_dead_pid_before_connecting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.tasks import worker_health

    pidfile = tmp_path / "worker.pid"
    pidfile.write_text("100\n", encoding="utf-8")
    connection = Mock()
    monkeypatch.setattr(worker_health.os, "kill", Mock(side_effect=ProcessLookupError))
    monkeypatch.setattr(worker_health.redis.Redis, "from_url", connection)
    with pytest.raises(ProcessLookupError):
        worker_health.check_worker_health("catalog", pidfile)
    connection.assert_not_called()
