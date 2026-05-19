"""Celery application. Broker and result backend share Redis."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "aurum",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.auth"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "expire-email-codes": {
        "task": "auth.expire_email_codes",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "expire-sessions": {
        "task": "auth.expire_sessions",
        "schedule": crontab(minute=0, hour=3),
    },
}
