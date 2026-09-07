"""Celery application for the isolated default maintenance worker."""

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import configure_task_app

celery_app = Celery(
    "aurum",
    broker=get_celery_broker_settings("system").CELERY_BROKER_URL,
    include=[
        "app.tasks.auth",
        "app.tasks.foundation",
        "app.tasks.roles",
        "app.tasks.notifications",
    ],
)
configure_task_app(celery_app, queues=("default",), worker_queue="default")
