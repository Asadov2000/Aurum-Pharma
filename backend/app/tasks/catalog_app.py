"""Celery application restricted to tenant-scoped catalog imports."""

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings

settings = get_celery_broker_settings()
catalog_app = Celery(
    "aurum-catalog-worker",
    broker=settings.REDIS_URL,
    include=["app.tasks.catalog"],
)
catalog_app.conf.update(
    task_default_queue="catalog-worker",
    task_routes={"catalog.import_catalog_job": {"queue": "catalog-worker"}},
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_ignore_result=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
