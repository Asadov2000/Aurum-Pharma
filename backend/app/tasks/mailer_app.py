"""Celery application restricted to the platform-mailer queue."""

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import MAILER_ROUTES, configure_task_app

mailer_app = Celery(
    "aurum-mailer-worker",
    broker=get_celery_broker_settings("mailer").CELERY_BROKER_URL,
    include=["app.tasks.auth_mailer", "app.tasks.platform_accounts"],
)
configure_task_app(mailer_app, queues=("platform-mailer",), worker_queue="platform-mailer")
mailer_app.conf.task_routes = MAILER_ROUTES
