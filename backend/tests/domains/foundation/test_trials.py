"""Automatic trial activation uses the canonical readiness transition."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.tasks.foundation import _auto_start_trials_async


async def test_auto_start_trials_promotes_only_ready_expired_setup(
    db_connection: AsyncConnection,
) -> None:
    session_factory = async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    nick = uuid4().hex[:8]
    async with session_factory() as session, session.begin():
        inserted = await session.execute(
            text("""
                INSERT INTO public.tenant (name, contact_email, status)
                VALUES
                  ('Auto Ready', :ready_email, 'setup'),
                  ('Auto Blocked', :blocked_email, 'setup'),
                  ('Auto Young', :young_email, 'setup')
                RETURNING id
                """),
            {
                "ready_email": f"auto-ready-{nick}@aurum.tj",
                "blocked_email": f"auto-blocked-{nick}@aurum.tj",
                "young_email": f"auto-young-{nick}@aurum.tj",
            },
        )
        ready_id, blocked_id, young_id = (UUID(str(row[0])) for row in inserted.fetchall())
        await session.execute(
            text("""
                INSERT INTO public.tenant_settings (tenant_id)
                VALUES (:ready), (:blocked), (:young)
                """),
            {"ready": ready_id, "blocked": blocked_id, "young": young_id},
        )
        await session.execute(
            text("""
                INSERT INTO public.onboarding_checklist (tenant_id, setup_ends_at)
                VALUES
                  (:ready, :expired),
                  (:blocked, :expired),
                  (:young, :future)
                """),
            {
                "ready": ready_id,
                "blocked": blocked_id,
                "young": young_id,
                "expired": utc_now() - timedelta(minutes=1),
                "future": utc_now() + timedelta(days=1),
            },
        )
        branch_id = (
            await session.execute(
                text("""
                    INSERT INTO public.branch (
                      tenant_id, name, address, license_number,
                      license_expires_at, receipt_header
                    ) VALUES (
                      :tenant_id, 'Main', 'Dushanbe', :license_number,
                      :license_expires_at, '{"line1":"Auto Ready"}'::jsonb
                    )
                    RETURNING id
                    """),
                {
                    "tenant_id": ready_id,
                    "license_number": f"AUTO-{nick}",
                    "license_expires_at": date.today() + timedelta(days=365),
                },
            )
        ).scalar_one()
        await session.execute(
            text("""
                INSERT INTO public.register (tenant_id, branch_id, name)
                VALUES (:tenant_id, :branch_id, 'Register 1')
                """),
            {"tenant_id": ready_id, "branch_id": branch_id},
        )
        user_id = (
            await session.execute(
                text("""
                    INSERT INTO public.app_user (
                      email, full_name, status, home_tenant_id, activated_at
                    ) VALUES (
                      :email, 'Auto Owner', 'active', :tenant_id,
                      statement_timestamp()
                    )
                    RETURNING id
                    """),
                {"email": f"auto-owner-{nick}@aurum.tj", "tenant_id": ready_id},
            )
        ).scalar_one()
        membership_id = (
            await session.execute(
                text("""
                    INSERT INTO public.tenant_membership (
                      tenant_id, user_id, full_name, status, activated_at
                    ) VALUES (
                      :tenant_id, :user_id, 'Auto Owner', 'active',
                      statement_timestamp()
                    )
                    RETURNING id
                    """),
                {"tenant_id": ready_id, "user_id": user_id},
            )
        ).scalar_one()
        await session.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  tenant_id, membership_id, is_active
                ) VALUES (:tenant_id, :membership_id, true)
                """),
            {"tenant_id": ready_id, "membership_id": membership_id},
        )
        await session.execute(
            text("""
                INSERT INTO public.tenant_catalog (
                  tenant_id, brand_name, dispensing_type, storage_type, is_active
                )
                SELECT
                  :tenant_id,
                  'Auto item ' || series.item,
                  'otc',
                  'normal',
                  true
                FROM pg_catalog.generate_series(1, 100) AS series(item)
                """),
            {"tenant_id": ready_id},
        )

    result = await _auto_start_trials_async(
        session_factory=session_factory,
        candidate_tenant_ids=frozenset({ready_id, blocked_id, young_id}),
    )
    assert result == {"started": 1, "skipped": 1}

    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, status, trial_started_at FROM public.tenant "
                    "WHERE id = ANY(:ids) ORDER BY name"
                ),
                {"ids": [ready_id, blocked_id, young_id]},
            )
        ).fetchall()
    by_id = {UUID(str(row[0])): (row[1], row[2]) for row in rows}
    assert by_id[ready_id][0] == "trial"
    assert by_id[ready_id][1] is not None
    assert by_id[blocked_id][0] == "setup"
    assert by_id[young_id][0] == "setup"
