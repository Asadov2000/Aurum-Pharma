"""Celery application restricted to the billing-worker queue."""

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import BILLING_ROUTES, configure_task_app

billing_app = Celery(
    "aurum-billing-worker",
    broker=get_celery_broker_settings("billing").CELERY_BROKER_URL,
    include=["app.tasks.billing"],
)
configure_task_app(billing_app, queues=("billing-worker",), worker_queue="billing-worker")
billing_app.conf.task_routes = BILLING_ROUTES
billing_app.conf.task_reject_on_worker_lost = True
