"""Celery application restricted to the catalog-worker queue."""

from celery import Celery

from app.core.celery_broker_config import get_celery_broker_settings
from app.tasks.broker_contract import CATALOG_ROUTES, configure_task_app

catalog_app = Celery(
    "aurum-catalog-worker",
    broker=get_celery_broker_settings("catalog").CELERY_BROKER_URL,
    include=["app.tasks.catalog"],
)
configure_task_app(catalog_app, queues=("catalog-worker",), worker_queue="catalog-worker")
catalog_app.conf.task_routes = CATALOG_ROUTES
catalog_app.conf.task_reject_on_worker_lost = True
