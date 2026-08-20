"""Celery tasks for non-financial billing state transitions.

Invoice and payment commands are intentionally absent: financial writes go
through the immutable ledger contract and its dedicated worker boundary.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy.exc import DBAPIError

from app.core.billing_worker_config import get_billing_worker_settings
from app.core.billing_worker_db import BillingWorkerSessionLocal
from app.domains.billing.repository import BillingWorkerRepository
from app.domains.billing.service import BillingWorkerService
from app.tasks.billing_app import billing_app

logger = structlog.get_logger("tasks.billing")
settings = get_billing_worker_settings()


async def _process_trial_endings() -> int:
    async with BillingWorkerSessionLocal() as db:
        async with db.begin():
            service = BillingWorkerService(BillingWorkerRepository(db))
            return await service.process_trial_endings(limit=settings.BILLING_TRANSITION_BATCH_SIZE)


async def _process_grace_endings() -> int:
    async with BillingWorkerSessionLocal() as db:
        async with db.begin():
            service = BillingWorkerService(BillingWorkerRepository(db))
            return await service.process_grace_endings(limit=settings.BILLING_TRANSITION_BATCH_SIZE)


@billing_app.task(  # type: ignore[misc]
    name="billing.process_trial_endings",
    autoretry_for=(DBAPIError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)
def process_trial_endings() -> int:
    moved = asyncio.run(_process_trial_endings())
    logger.info("trial_endings_done", moved=moved)
    return moved


@billing_app.task(  # type: ignore[misc]
    name="billing.process_grace_endings",
    autoretry_for=(DBAPIError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)
def process_grace_endings() -> int:
    moved = asyncio.run(_process_grace_endings())
    logger.info("grace_endings_done", moved=moved)
    return moved
