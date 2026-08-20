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
    include=[
        "app.tasks.auth",
        "app.tasks.foundation",
        "app.tasks.roles",
        "app.tasks.catalog",
        "app.tasks.notifications",
    ],
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
    task_default_queue="default",
    task_routes={
        "platform_accounts.process_invitation_emails": {"queue": "platform-mailer"},
        "billing.process_trial_endings": {"queue": "billing-worker"},
        "billing.process_grace_endings": {"queue": "billing-worker"},
    },
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
    "auto-start-trials": {
        "task": "foundation.auto_start_trials",
        "schedule": crontab(minute=0, hour=4),
    },
    "billing-process-trial-endings": {
        "task": "billing.process_trial_endings",
        "schedule": crontab(minute=0, hour=6),
    },
    "billing-process-grace-endings": {
        "task": "billing.process_grace_endings",
        "schedule": crontab(minute=0, hour=7),
    },
    "notifications-process-pending-deliveries": {
        "task": "notifications.process_pending_deliveries",
        "schedule": crontab(minute="*"),
    },
    "platform-accounts-process-invitation-emails": {
        "task": "platform_accounts.process_invitation_emails",
        "schedule": crontab(minute="*"),
    },
    "notifications-check-expiring-licenses": {
        "task": "notifications.check_expiring_licenses",
        "schedule": crontab(minute=0, hour=8),
    },
    "notifications-purge-old": {
        "task": "notifications.purge_old_notifications",
        # Mondays at 02:00 UTC
        "schedule": crontab(minute=0, hour=2, day_of_week=1),
    },
}
