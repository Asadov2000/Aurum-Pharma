"""Celery application that can consume only the isolated mailer queue."""

from celery import Celery

from app.core.mailer_config import get_mailer_settings

settings = get_mailer_settings()
mailer_app = Celery(
    "aurum-mailer",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.platform_accounts"],
)
mailer_app.conf.update(
    task_default_queue="platform-mailer",
    task_routes={"platform_accounts.process_invitation_emails": {"queue": "platform-mailer"}},
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
