"""Database boundaries for the closed versioned billing pricing foundation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

PRICING_TABLES = {
    "billing_contract_override",
    "billing_plan",
    "billing_price_version",
}


@pytest_asyncio.fixture
async def support_engine_billing_pricing() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pricing_foundation_is_closed_and_uses_exact_money_types(
    support_engine_billing_pricing: AsyncEngine,
) -> None:
    async with support_engine_billing_pricing.connect() as connection:
        relations = list(
            (
                await connection.execute(
                    text("""
                        SELECT
                          relations.relname,
                          pg_get_userbyid(relations.relowner) AS owner,
                          relations.relrowsecurity,
                          relations.relforcerowsecurity
                        FROM pg_catalog.pg_class AS relations
                        WHERE relations.relnamespace = 'public'::regnamespace
                          AND relations.relname = ANY(:tables)
                        ORDER BY relations.relname
                        """),
                    {"tables": sorted(PRICING_TABLES)},
                )
            ).mappings()
        )
        policies = list(
            (
                await connection.execute(
                    text("""
                        SELECT tablename, policyname
                        FROM pg_catalog.pg_policies
                        WHERE schemaname = 'public'
                          AND tablename = ANY(:tables)
                        """),
                    {"tables": sorted(PRICING_TABLES)},
                )
            ).mappings()
        )
        privileges = list(
            (
                await connection.execute(
                    text("""
                        SELECT
                          relations.relname,
                          roles.role_name,
                          checks.privilege,
                          has_table_privilege(
                            roles.role_name,
                            relations.oid,
                            checks.privilege
                          ) AS allowed
                        FROM pg_catalog.pg_class AS relations
                        CROSS JOIN (VALUES ('aurum_app'), ('aurum_support'))
                          AS roles(role_name)
                        CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'))
                          AS checks(privilege)
                        WHERE relations.relnamespace = 'public'::regnamespace
                          AND relations.relname = ANY(:tables)
                        """),
                    {"tables": sorted(PRICING_TABLES)},
                )
            ).mappings()
        )
        money_columns = list(
            (
                await connection.execute(
                    text("""
                        SELECT
                          relations.relname AS table_name,
                          attributes.attname AS column_name,
                          format_type(attributes.atttypid, attributes.atttypmod) AS data_type
                        FROM pg_catalog.pg_class AS relations
                        JOIN pg_catalog.pg_attribute AS attributes
                          ON attributes.attrelid = relations.oid
                        WHERE relations.relnamespace = 'public'::regnamespace
                          AND relations.relname = ANY(:tables)
                          AND attributes.attname = 'monthly_price_per_branch'
                          AND NOT attributes.attisdropped
                        ORDER BY relations.relname
                        """),
                    {"tables": sorted(PRICING_TABLES)},
                )
            ).mappings()
        )

    assert {row["relname"] for row in relations} == PRICING_TABLES
    assert all(row["owner"] == "aurum_schema_owner" for row in relations)
    assert {
        row["relname"] for row in relations if row["relrowsecurity"] and row["relforcerowsecurity"]
    } == {"billing_contract_override"}
    assert policies == []
    assert not any(row["allowed"] for row in privileges)
    assert money_columns == [
        {
            "table_name": "billing_contract_override",
            "column_name": "monthly_price_per_branch",
            "data_type": "numeric(14,2)",
        },
        {
            "table_name": "billing_price_version",
            "column_name": "monthly_price_per_branch",
            "data_type": "numeric(14,2)",
        },
    ]


async def test_published_price_terms_are_immutable(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            users = (
                (
                    await connection.execute(
                        text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES
                          (:author_email, 'Pricing author', 'active'),
                          (:approver_email, 'Pricing approver', 'active')
                        RETURNING id
                        """),
                        {
                            "author_email": f"pricing-author-{suffix}@example.invalid",
                            "approver_email": f"pricing-approver-{suffix}@example.invalid",
                        },
                    )
                )
                .scalars()
                .all()
            )
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan
                          (code, name, created_by)
                        VALUES (:code, 'Aurum pricing test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"pricing_{suffix}", "author_id": users[0]},
                )
            ).scalar_one()
            price_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_price_version (
                          plan_id, version_number, monthly_price_per_branch,
                          created_by
                        ) VALUES (:plan_id, 1, 550.00, :author_id)
                        RETURNING id
                        """),
                    {"plan_id": plan_id, "author_id": users[0]},
                )
            ).scalar_one()
            row_version = (
                await connection.execute(
                    text("""
                        UPDATE public.billing_price_version
                        SET status = 'scheduled',
                            effective_from = now() + interval '40 days',
                            reason = 'Planned commercial update',
                            approved_by = :approver_id,
                            approved_at = now()
                        WHERE id = :price_id
                        RETURNING row_version
                        """),
                    {"price_id": price_id, "approver_id": users[1]},
                )
            ).scalar_one()
            assert row_version == 2

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(DBAPIError, match="Scheduled billing price terms are immutable"):
                    await connection.execute(
                        text("""
                            UPDATE public.billing_price_version
                            SET monthly_price_per_branch = 1.00
                            WHERE id = :price_id
                            """),
                        {"price_id": price_id},
                    )
            finally:
                await savepoint.rollback()

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError, match="Published billing price versions are immutable"
                ):
                    await connection.execute(
                        text("DELETE FROM public.billing_price_version WHERE id = :price_id"),
                        {"price_id": price_id},
                    )
            finally:
                await savepoint.rollback()
        finally:
            await transaction.rollback()


async def test_price_publication_requires_separate_approver(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            author_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES (:email, 'Pricing author', 'active')
                        RETURNING id
                        """),
                    {"email": f"pricing-self-approval-{suffix}@example.invalid"},
                )
            ).scalar_one()
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan (code, name, created_by)
                        VALUES (:code, 'Approval test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"approval_{suffix}", "author_id": author_id},
                )
            ).scalar_one()
            price_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_price_version (
                          plan_id, version_number, monthly_price_per_branch,
                          created_by
                        ) VALUES (:plan_id, 1, 550.00, :author_id)
                        RETURNING id
                        """),
                    {"plan_id": plan_id, "author_id": author_id},
                )
            ).scalar_one()

            with pytest.raises(DBAPIError) as self_approval_error:
                await connection.execute(
                    text("""
                        UPDATE public.billing_price_version
                        SET status = 'scheduled',
                            effective_from = now() + interval '40 days',
                            reason = 'Invalid self approval',
                            approved_by = :author_id,
                            approved_at = now()
                        WHERE id = :price_id
                        """),
                    {"price_id": price_id, "author_id": author_id},
                )
            assert getattr(self_approval_error.value.orig, "sqlstate", None) == "23514"
            assert "ck_billing_price_separation" in str(self_approval_error.value)
        finally:
            await transaction.rollback()


async def test_draft_price_cannot_retain_approval_or_activate_early(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            users = (
                (
                    await connection.execute(
                        text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES
                          (:author_email, 'Pricing author', 'active'),
                          (:approver_email, 'Pricing approver', 'active')
                        RETURNING id
                        """),
                        {
                            "author_email": f"pricing-draft-{suffix}@example.invalid",
                            "approver_email": f"pricing-review-{suffix}@example.invalid",
                        },
                    )
                )
                .scalars()
                .all()
            )
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan (code, name, created_by)
                        VALUES (:code, 'Draft approval test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"draft_{suffix}", "author_id": users[0]},
                )
            ).scalar_one()
            price_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_price_version (
                          plan_id, version_number, monthly_price_per_branch,
                          created_by
                        ) VALUES (:plan_id, 1, 550.00, :author_id)
                        RETURNING id
                        """),
                    {"plan_id": plan_id, "author_id": users[0]},
                )
            ).scalar_one()

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError, match="Draft billing price versions cannot retain approval"
                ):
                    await connection.execute(
                        text("""
                            UPDATE public.billing_price_version
                            SET approved_by = :approver_id, approved_at = now()
                            WHERE id = :price_id
                            """),
                        {"price_id": price_id, "approver_id": users[1]},
                    )
            finally:
                await savepoint.rollback()

            await connection.execute(
                text("""
                    UPDATE public.billing_price_version
                    SET status = 'scheduled',
                        effective_from = now() + interval '40 days',
                        reason = 'Approved future price',
                        approved_by = :approver_id,
                        approved_at = now()
                    WHERE id = :price_id
                    """),
                {"price_id": price_id, "approver_id": users[1]},
            )
            with pytest.raises(
                DBAPIError, match="Billing price cannot be activated before its effective date"
            ):
                await connection.execute(
                    text("""
                        UPDATE public.billing_price_version
                        SET status = 'active', activated_at = now()
                        WHERE id = :price_id
                        """),
                    {"price_id": price_id},
                )
        finally:
            await transaction.rollback()


async def test_published_contract_override_terms_are_immutable(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            users = (
                (
                    await connection.execute(
                        text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES
                          (:author_email, 'Contract author', 'active'),
                          (:approver_email, 'Contract approver', 'active')
                        RETURNING id
                        """),
                        {
                            "author_email": f"contract-author-{suffix}@example.invalid",
                            "approver_email": f"contract-approver-{suffix}@example.invalid",
                        },
                    )
                )
                .scalars()
                .all()
            )
            tenant_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.tenant (name, contact_email)
                        VALUES ('Contract test tenant', :email)
                        RETURNING id
                        """),
                    {"email": f"contract-tenant-{suffix}@example.invalid"},
                )
            ).scalar_one()
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan (code, name, created_by)
                        VALUES (:code, 'Contract override test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"contract_{suffix}", "author_id": users[0]},
                )
            ).scalar_one()
            override_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_contract_override (
                          tenant_id, plan_id, monthly_price_per_branch, created_by
                        ) VALUES (:tenant_id, :plan_id, 500.00, :author_id)
                        RETURNING id
                        """),
                    {
                        "tenant_id": tenant_id,
                        "plan_id": plan_id,
                        "author_id": users[0],
                    },
                )
            ).scalar_one()
            await connection.execute(
                text("""
                    UPDATE public.billing_contract_override
                    SET status = 'scheduled',
                        valid_from = now() + interval '7 days',
                        reason = 'Approved individual terms',
                        approved_by = :approver_id,
                        approved_at = now()
                    WHERE id = :override_id
                    """),
                {"override_id": override_id, "approver_id": users[1]},
            )

            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(
                    DBAPIError, match="Scheduled billing contract terms are immutable"
                ):
                    await connection.execute(
                        text("""
                            UPDATE public.billing_contract_override
                            SET monthly_price_per_branch = 1.00
                            WHERE id = :override_id
                            """),
                        {"override_id": override_id},
                    )
            finally:
                await savepoint.rollback()

            with pytest.raises(
                DBAPIError, match="Billing contract cannot be activated before its start date"
            ):
                await connection.execute(
                    text("""
                        UPDATE public.billing_contract_override
                        SET status = 'active', activated_at = now()
                        WHERE id = :override_id
                        """),
                    {"override_id": override_id},
                )
        finally:
            await transaction.rollback()


@pytest.mark.parametrize(
    ("column", "value_sql", "constraint_name"),
    [
        ("monthly_price_per_branch", "-0.01", "ck_billing_price_amount"),
        ("annual_discount_pct", "100.00", "ck_billing_price_discount"),
        ("terms_snapshot", "'[]'::jsonb", "ck_billing_price_terms_snapshot"),
        ("notice_days", "0", "ck_billing_price_notice_days"),
    ],
)
async def test_invalid_price_terms_are_rejected_by_database(
    maintenance_engine: AsyncEngine,
    column: str,
    value_sql: str,
    constraint_name: str,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            author_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES (:email, 'Invalid pricing author', 'active')
                        RETURNING id
                        """),
                    {"email": f"pricing-invalid-{suffix}@example.invalid"},
                )
            ).scalar_one()
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan (code, name, created_by)
                        VALUES (:code, 'Invalid terms test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"invalid_{suffix}", "author_id": author_id},
                )
            ).scalar_one()

            statement = text(f"""
                INSERT INTO public.billing_price_version (
                  plan_id, version_number, monthly_price_per_branch,
                  annual_discount_pct, terms_snapshot, created_by, notice_days
                ) VALUES (
                  :plan_id, 1,
                  {value_sql if column == 'monthly_price_per_branch' else '550.00'},
                  {value_sql if column == 'annual_discount_pct' else '20.00'},
                  {value_sql if column == 'terms_snapshot' else "'{}'::jsonb"},
                  :author_id,
                  {value_sql if column == 'notice_days' else '30'}
                )
                """)
            with pytest.raises(DBAPIError) as invalid_terms_error:
                await connection.execute(
                    statement,
                    {"plan_id": plan_id, "author_id": author_id},
                )
            assert getattr(invalid_terms_error.value.orig, "sqlstate", None) == "23514"
            assert constraint_name in str(invalid_terms_error.value)
        finally:
            await transaction.rollback()


async def test_duplicate_price_version_is_rejected(
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            author_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.app_user (email, full_name, status)
                        VALUES (:email, 'Duplicate pricing author', 'active')
                        RETURNING id
                        """),
                    {"email": f"pricing-duplicate-{suffix}@example.invalid"},
                )
            ).scalar_one()
            plan_id = (
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_plan (code, name, created_by)
                        VALUES (:code, 'Duplicate version test', :author_id)
                        RETURNING id
                        """),
                    {"code": f"duplicate_{suffix}", "author_id": author_id},
                )
            ).scalar_one()
            await connection.execute(
                text("""
                    INSERT INTO public.billing_price_version (
                      plan_id, version_number, monthly_price_per_branch, created_by
                    ) VALUES (:plan_id, 1, 550.00, :author_id)
                    """),
                {"plan_id": plan_id, "author_id": author_id},
            )

            with pytest.raises(DBAPIError) as duplicate_error:
                await connection.execute(
                    text("""
                        INSERT INTO public.billing_price_version (
                          plan_id, version_number, monthly_price_per_branch, created_by
                        ) VALUES (:plan_id, 1, 600.00, :author_id)
                        """),
                    {"plan_id": plan_id, "author_id": author_id},
                )
            assert getattr(duplicate_error.value.orig, "sqlstate", None) == "23505"
            assert "uq_billing_price_plan_version" in str(duplicate_error.value)
        finally:
            await transaction.rollback()
