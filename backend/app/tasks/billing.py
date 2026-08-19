"""Celery tasks for non-financial billing state transitions.

Invoice and payment commands are intentionally absent: financial writes go
through the immutable ledger contract and its dedicated worker boundary.
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.db import WorkerSessionLocal
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.billing")


async def _process_trial_endings() -> int:
    async with WorkerSessionLocal() as db:
        async with db.begin():
            service = BillingService(BillingRepository(db))
            return await service.process_trial_endings()


async def _process_grace_endings() -> int:
    async with WorkerSessionLocal() as db:
        async with db.begin():
            service = BillingService(BillingRepository(db))
            return await service.process_grace_endings()


@celery_app.task(name="billing.process_trial_endings")  # type: ignore[misc]
def process_trial_endings() -> int:
    moved = asyncio.run(_process_trial_endings())
    logger.info("trial_endings_done", moved=moved)
    return moved


@celery_app.task(name="billing.process_grace_endings")  # type: ignore[misc]
def process_grace_endings() -> int:
    moved = asyncio.run(_process_grace_endings())
    logger.info("grace_endings_done", moved=moved)
    return moved
