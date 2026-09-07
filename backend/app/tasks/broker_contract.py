"""Queue names and acknowledgement namespaces shared by producers and workers."""

from celery import Celery

# Kombu is Celery's installed transport dependency and does not publish typing stubs.
from kombu import Exchange, Queue  # type: ignore[import-untyped]

CATALOG_ROUTES = {
    "catalog.import_catalog_job": {"queue": "catalog-worker"},
    "catalog.preview_catalog_job": {"queue": "catalog-worker"},
}
BILLING_ROUTES = {
    "billing.process_trial_endings": {"queue": "billing-worker"},
    "billing.process_grace_endings": {"queue": "billing-worker"},
}
MAILER_ROUTES = {
    "auth.process_login_emails": {"queue": "platform-mailer"},
    "platform_accounts.process_invitation_emails": {"queue": "platform-mailer"},
}


def configure_task_app(
    app: Celery, *, queues: tuple[str, ...], worker_queue: str | None = None
) -> None:
    app.conf.update(
        task_queues=tuple(
            Queue(name, Exchange(name, type="direct"), routing_key=name) for name in queues
        ),
        task_default_queue=queues[0],
        task_create_missing_queues=False,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_track_started=False,
        result_backend=None,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
    )
    if worker_queue is not None:
        app.conf.broker_transport_options = {
            "unacked_key": f"aurum:broker:{worker_queue}:unacked",
            "unacked_index_key": f"aurum:broker:{worker_queue}:unacked_index",
            "unacked_mutex_key": f"aurum:broker:{worker_queue}:unacked_mutex",
        }
