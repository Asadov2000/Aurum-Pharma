"""auto_start_trials: tenants older than 60 days in 'setup' get promoted."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.time import utc_now
from app.tasks.foundation import _auto_start_trials_async


@pytest_asyncio.fixture
async def support_engine_trials() -> AsyncIterator[AsyncEngine]:
    """A dedicated support-pool engine so seeded rows are committed and
    visible to the standalone session opened inside _auto_start_trials_async."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_auto_start_trials_promotes_stale_setup(
    support_engine_trials: AsyncEngine,
) -> None:
    """Insert one stale tenant + one young one (both 'setup'), back-date
    the stale one by 61 days, run the job, then verify only the stale
    tenant moved to 'trial'."""

    tenant_ids: list[str] = []
    try:
        # ---- seed two tenants under support pool ----
        async with support_engine_trials.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "('Stale', 'stale@aurum.tj', 'setup'), "
                    "('Young', 'young@aurum.tj', 'setup') "
                    "RETURNING id"
                )
            )
            tenant_ids = [str(row[0]) for row in result.fetchall()]
            stale_id, young_id = tenant_ids[0], tenant_ids[1]

            # Back-date the stale tenant by 61 days
            await conn.execute(
                text("UPDATE tenant SET created_at = :ts WHERE id = :id"),
                {"ts": utc_now() - timedelta(days=61), "id": stale_id},
            )

        # ---- run the Celery task body inline ----
        started = await _auto_start_trials_async()
        assert started == 1, f"expected exactly one stale tenant promoted, got {started}"

        # ---- verify statuses ----
        async with support_engine_trials.connect() as conn:
            stale_row = (
                await conn.execute(
                    text(
                        "SELECT status, trial_started_at, trial_ends_at "
                        "FROM tenant WHERE id = :id"
                    ),
                    {"id": stale_id},
                )
            ).first()
            young_row = (
                await conn.execute(
                    text("SELECT status FROM tenant WHERE id = :id"),
                    {"id": young_id},
                )
            ).first()

        assert stale_row is not None and young_row is not None
        assert stale_row[0] == "trial"
        assert stale_row[1] is not None
        assert stale_row[2] is not None
        assert young_row[0] == "setup"
    finally:
        if tenant_ids:
            async with support_engine_trials.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant_settings WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
