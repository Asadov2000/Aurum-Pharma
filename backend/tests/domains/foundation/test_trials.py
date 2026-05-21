"""auto_start_trials: tenants older than 60 days in 'setup' get promoted,
but only if their catalogue has at least 100 items.

The task opens its own session against the module-level SupportSessionLocal,
which is bound to a single asyncio loop. We keep the test as one function
so the engine is only ever touched from one loop.
"""

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
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_auto_start_trials_promotes_only_filled_catalogs(
    support_engine_trials: AsyncEngine,
) -> None:
    """Three stale tenants + one young one. Stale_filled has catalog=100
    and gets promoted; stale_empty has catalog=0 and is skipped; young_filled
    has catalog=100 but isn't old enough."""
    tenant_ids: list[str] = []
    try:
        async with support_engine_trials.begin() as conn:
            inserted = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "('StaleFilled','sf@aurum.tj','setup'),"
                    "('StaleEmpty','se@aurum.tj','setup'),"
                    "('YoungFilled','yf@aurum.tj','setup') "
                    "RETURNING id"
                )
            )
            ids = [str(r[0]) for r in inserted.fetchall()]
            stale_filled, stale_empty, young_filled = ids
            tenant_ids = ids

            # Back-date the two stale tenants
            await conn.execute(
                text("UPDATE tenant SET created_at = :ts WHERE id = ANY(:ids)"),
                {"ts": utc_now() - timedelta(days=61), "ids": [stale_filled, stale_empty]},
            )

            # Checklists for all three; only the two "Filled" have catalog=100
            await conn.execute(
                text(
                    "INSERT INTO onboarding_checklist "
                    "(tenant_id, setup_ends_at, catalog_items_count) VALUES "
                    "(:sf, :ts, 100), (:se, :ts, 0), (:yf, :ts, 100)"
                ),
                {
                    "sf": stale_filled,
                    "se": stale_empty,
                    "yf": young_filled,
                    "ts": utc_now() + timedelta(days=60),
                },
            )

        result = await _auto_start_trials_async()
        assert result == {"started": 1, "skipped": 1}, result

        async with support_engine_trials.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, status, trial_started_at FROM tenant "
                        "WHERE id = ANY(:ids) ORDER BY name"
                    ),
                    {"ids": tenant_ids},
                )
            ).fetchall()
        by_id = {str(r[0]): (r[1], r[2]) for r in rows}
        assert by_id[stale_filled][0] == "trial"
        assert by_id[stale_filled][1] is not None
        assert by_id[stale_empty][0] == "setup"
        assert by_id[young_filled][0] == "setup"
    finally:
        if tenant_ids:
            async with support_engine_trials.begin() as conn:
                await conn.execute(
                    text("DELETE FROM onboarding_checklist WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant_settings WHERE tenant_id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:ids)"),
                    {"ids": tenant_ids},
                )
