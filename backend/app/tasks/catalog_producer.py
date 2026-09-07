"""API dispatch without importing catalog worker database or storage settings."""

from typing import Literal
from uuid import UUID

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import CATALOG_ROUTES, configure_task_app

producer_app = Celery("aurum-api-producer", broker=get_celery_broker_settings().CELERY_BROKER_URL)
configure_task_app(producer_app, queues=("catalog-worker",))
producer_app.conf.task_routes = CATALOG_ROUTES


def enqueue_catalog_job(
    phase: Literal["preview", "import"], job_id: UUID, tenant_id: UUID, execution_id: UUID
) -> None:
    task_name = {
        "preview": "catalog.preview_catalog_job",
        "import": "catalog.import_catalog_job",
    }[phase]
    producer_app.send_task(
        task_name,
        args=(str(job_id), str(tenant_id), str(execution_id)),
        ignore_result=True,
    )
