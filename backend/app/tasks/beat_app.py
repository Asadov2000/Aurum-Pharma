"""Scheduler can publish maintenance tasks without loading worker settings."""

from celery import Celery
from celery.schedules import crontab

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import BILLING_ROUTES, MAILER_ROUTES, configure_task_app

beat_app = Celery("aurum-beat", broker=get_celery_broker_settings("beat").CELERY_BROKER_URL)
configure_task_app(beat_app, queues=("default", "billing-worker", "platform-mailer"))
beat_app.conf.task_routes = {**BILLING_ROUTES, **MAILER_ROUTES}

beat_app.conf.beat_schedule = {
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
    "platform-accounts-process-invitation-emails": {
        "task": "platform_accounts.process_invitation_emails",
        "schedule": crontab(minute="*"),
    },
    "auth-process-login-emails": {
        "task": "auth.process_login_emails",
        "schedule": 5.0,
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
