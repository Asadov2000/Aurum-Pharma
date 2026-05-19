"""Celery tasks for the roles domain.

Phase 1: send_invite_email only writes the invite to logs — a real SMTP
provider is wired up in phase 2 (call signature does not change).
"""

from __future__ import annotations

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.roles")


@celery_app.task(name="roles.send_invite_email")  # type: ignore[misc]
def send_invite_email(email: str, tenant_id: str) -> None:
    logger.info("send_invite_email", email=email, tenant_id=tenant_id)
