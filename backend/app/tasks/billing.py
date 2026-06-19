"""Celery tasks for the billing domain.

All three jobs run daily from the beat schedule. Each spawns its own
support-pool session — there's no HTTP request context to seed RLS GUCs,
and the service operates across tenants (the only billing actor at this
stage is the platform itself).
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.db import WorkerSessionLocal
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.billing")


async def _generate_invoices() -> int:
    async with WorkerSessionLocal() as db:
        async with db.begin():
            service = BillingService(BillingRepository(db))
            return await service.generate_monthly_invoices()


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


async def _recalc(tenant_id: str) -> None:
    from uuid import UUID

    async with WorkerSessionLocal() as db:
        async with db.begin():
            service = BillingService(BillingRepository(db))
            await service.recalculate_on_branch_change(UUID(tenant_id))


@celery_app.task(name="billing.generate_monthly_invoices")  # type: ignore[misc]
def generate_monthly_invoices() -> int:
    issued = asyncio.run(_generate_invoices())
    logger.info("generate_monthly_invoices_done", issued=issued)
    return issued


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


@celery_app.task(name="billing.recalculate_subscription_on_branch_change")  # type: ignore[misc]
def recalculate_subscription_on_branch_change(tenant_id: str) -> None:
    asyncio.run(_recalc(tenant_id))
    logger.info("subscription_recalc_done", tenant_id=tenant_id)
