"""Celery application that can consume only the isolated billing queue."""

from celery import Celery

from app.core.billing_worker_config import get_billing_worker_settings

settings = get_billing_worker_settings()
billing_app = Celery(
    "aurum-billing-worker",
    broker=settings.REDIS_URL,
    include=["app.tasks.billing"],
)
billing_app.conf.update(
    task_default_queue="billing-worker",
    task_routes={
        "billing.process_trial_endings": {"queue": "billing-worker"},
        "billing.process_grace_endings": {"queue": "billing-worker"},
    },
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
