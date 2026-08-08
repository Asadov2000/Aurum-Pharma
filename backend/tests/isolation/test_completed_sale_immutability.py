"""Database boundary for immutable finalized sales and their components."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def app_engine_sale_guard() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_engine_sale_guard() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_tenant(connection: AsyncConnection, tenant_id: UUID) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def _seed_finalized_sale(engine: AsyncEngine) -> dict[str, UUID]:
    suffix = uuid4().hex[:12]
    async with engine.begin() as connection:
        tenant_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.tenant (name, contact_email, status) "
                    "VALUES (:name, :email, 'active') RETURNING id"
                ),
                {
                    "name": f"Sale guard {suffix}",
                    "email": f"sale-guard-{suffix}@example.invalid",
                },
            )
        ).scalar_one()
        await _set_tenant(connection, tenant_id)
        branch_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.branch (tenant_id, name) "
                    "VALUES (:tenant_id, 'Main') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        register_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.register (tenant_id, branch_id, name) "
                    "VALUES (:tenant_id, :branch_id, 'Register') RETURNING id"
                ),
                {"tenant_id": tenant_id, "branch_id": branch_id},
            )
        ).scalar_one()
        user_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.app_user (email, full_name, status) "
                    "VALUES (:email, 'Cashier', 'active') RETURNING id"
                ),
                {"email": f"sale-guard-user-{suffix}@example.invalid"},
            )
        ).scalar_one()
        catalog_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.tenant_catalog (tenant_id, brand_name) "
                    "VALUES (:tenant_id, 'Guard item') RETURNING id"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        batch_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.batch ("
                    "tenant_id, branch_id, catalog_id, expires_at, purchase_price, "
                    "sale_price, qty_initial, qty_remaining"
                    ") VALUES ("
                    ":tenant_id, :branch_id, :catalog_id, CURRENT_DATE + 365, "
                    "5, 10, 20, 20"
                    ") RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "catalog_id": catalog_id,
                },
            )
        ).scalar_one()
        shift_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.shift ("
                    "tenant_id, branch_id, register_id, opened_by_user_id"
                    ") VALUES ("
                    ":tenant_id, :branch_id, :register_id, :user_id"
                    ") RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "register_id": register_id,
                    "user_id": user_id,
                },
            )
        ).scalar_one()
        sale_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.sale ("
                    "tenant_id, branch_id, register_id, shift_id, cashier_user_id, "
                    "status, total_amount"
                    ") VALUES ("
                    ":tenant_id, :branch_id, :register_id, :shift_id, :user_id, "
                    "'draft', 10"
                    ") RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "branch_id": branch_id,
                    "register_id": register_id,
                    "shift_id": shift_id,
                    "user_id": user_id,
                },
            )
        ).scalar_one()
        item_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.sale_item ("
                    "tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                    "total_price, position"
                    ") VALUES ("
                    ":tenant_id, :sale_id, :catalog_id, :batch_id, 1, 10, 10, 1"
                    ") RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "sale_id": sale_id,
                    "catalog_id": catalog_id,
                    "batch_id": batch_id,
                },
            )
        ).scalar_one()
        payment_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.sale_payment ("
                    "tenant_id, sale_id, payment_method, amount"
                    ") VALUES (:tenant_id, :sale_id, 'cash', 10) RETURNING id"
                ),
                {"tenant_id": tenant_id, "sale_id": sale_id},
            )
        ).scalar_one()
        prescription_id = (
            await connection.execute(
                text(
                    "INSERT INTO public.prescription_log ("
                    "tenant_id, sale_id, sale_item_id, prescription_number"
                    ") VALUES (:tenant_id, :sale_id, :item_id, 'RX-GUARD') RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "sale_id": sale_id,
                    "item_id": item_id,
                },
            )
        ).scalar_one()
        await connection.execute(
            text(
                "UPDATE public.sale SET "
                "status = 'completed', completed_at = now(), "
                "receipt_snapshot = '{}'::jsonb, "
                "receipt_number = 'GUARD-000001', receipt_seq = 1 "
                "WHERE id = :sale_id"
            ),
            {"sale_id": sale_id},
        )
    return {
        "tenant_id": tenant_id,
        "branch_id": branch_id,
        "register_id": register_id,
        "user_id": user_id,
        "catalog_id": catalog_id,
        "batch_id": batch_id,
        "shift_id": shift_id,
        "sale_id": sale_id,
        "item_id": item_id,
        "payment_id": payment_id,
        "prescription_id": prescription_id,
    }


async def _expect_rejected(
    engine: AsyncEngine,
    tenant_id: UUID,
    statement: str,
    params: dict[str, object],
) -> None:
    with pytest.raises(DBAPIError, match="Sale|sale") as error:
        async with engine.begin() as connection:
            await _set_tenant(connection, tenant_id)
            await connection.execute(text(statement), params)
    assert getattr(error.value.orig, "sqlstate", None) == "23514"


async def _cleanup(
    maintenance_engine: AsyncEngine,
    support_engine: AsyncEngine,
    ids: dict[str, UUID],
) -> None:
    guarded_tables = (
        ("batch_movement", "trg_guard_batch_movement_immutability"),
        ("prescription_log", "trg_guard_prescription_log_immutability"),
        ("sale_payment", "trg_guard_sale_payment_immutability"),
        ("sale_item", "trg_guard_sale_item_immutability"),
        ("sale", "trg_guard_sale_immutability"),
    )
    async with maintenance_engine.begin() as connection:
        for table, trigger in guarded_tables:
            await connection.execute(text(f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}"))

    try:
        async with support_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": ids["user_id"]},
            )
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": ids["tenant_id"]},
            )
    finally:
        async with maintenance_engine.begin() as connection:
            for table, trigger in reversed(guarded_tables):
                await connection.execute(
                    text(f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}")
                )


async def test_runtime_roles_cannot_mutate_finalized_sale_history(
    app_engine_sale_guard: AsyncEngine,
    support_engine_sale_guard: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    ids = await _seed_finalized_sale(support_engine_sale_guard)
    mutations = (
        (
            "UPDATE public.sale SET total_amount = 999 WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET completed_at = now() + interval '1 hour' "
            "WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET is_test = true WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET status = 'voided' WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET receipt_number = 'CHANGED' WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET receipt_snapshot = '{}'::jsonb WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale SET tenant_id = :other_tenant_id WHERE id = :sale_id",
            {"sale_id": ids["sale_id"], "other_tenant_id": uuid4()},
        ),
        (
            "DELETE FROM public.sale WHERE id = :sale_id",
            {"sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.sale_item SET qty = 2 WHERE id = :item_id",
            {"item_id": ids["item_id"]},
        ),
        (
            "DELETE FROM public.sale_item WHERE id = :item_id",
            {"item_id": ids["item_id"]},
        ),
        (
            "INSERT INTO public.sale_item ("
            "id, tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
            "total_price, position"
            ") VALUES ("
            ":id, :tenant_id, :sale_id, :catalog_id, :batch_id, 1, 10, 10, 2"
            ")",
            {
                "id": uuid4(),
                "tenant_id": ids["tenant_id"],
                "sale_id": ids["sale_id"],
                "catalog_id": ids["catalog_id"],
                "batch_id": ids["batch_id"],
            },
        ),
        (
            "UPDATE public.sale_payment SET amount = 999 WHERE id = :payment_id",
            {"payment_id": ids["payment_id"]},
        ),
        (
            "DELETE FROM public.sale_payment WHERE id = :payment_id",
            {"payment_id": ids["payment_id"]},
        ),
        (
            "INSERT INTO public.sale_payment ("
            "tenant_id, sale_id, payment_method, amount"
            ") VALUES (:tenant_id, :sale_id, 'cash', 1)",
            {"tenant_id": ids["tenant_id"], "sale_id": ids["sale_id"]},
        ),
        (
            "UPDATE public.prescription_log SET notes = 'changed' " "WHERE id = :prescription_id",
            {"prescription_id": ids["prescription_id"]},
        ),
        (
            "DELETE FROM public.prescription_log WHERE id = :prescription_id",
            {"prescription_id": ids["prescription_id"]},
        ),
        (
            "INSERT INTO public.prescription_log (tenant_id, sale_id, notes) "
            "VALUES (:tenant_id, :sale_id, 'late')",
            {"tenant_id": ids["tenant_id"], "sale_id": ids["sale_id"]},
        ),
        (
            "INSERT INTO public.sale ("
            "tenant_id, branch_id, register_id, shift_id, cashier_user_id, status"
            ") VALUES ("
            ":tenant_id, :branch_id, :register_id, :shift_id, :user_id, 'completed'"
            ")",
            {
                "tenant_id": ids["tenant_id"],
                "branch_id": ids["branch_id"],
                "register_id": ids["register_id"],
                "shift_id": ids["shift_id"],
                "user_id": ids["user_id"],
            },
        ),
    )
    try:
        for engine in (app_engine_sale_guard, support_engine_sale_guard):
            for statement, params in mutations:
                await _expect_rejected(
                    engine,
                    ids["tenant_id"],
                    statement,
                    params,
                )

        async with support_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            sale_snapshot = (
                await connection.execute(
                    text(
                        "SELECT status, total_amount, receipt_number, receipt_seq "
                        "FROM public.sale WHERE id = :sale_id"
                    ),
                    {"sale_id": ids["sale_id"]},
                )
            ).one()
            assert tuple(sale_snapshot) == ("completed", 10, "GUARD-000001", 1)
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM public.sale_item WHERE sale_id = :sale_id"),
                    {"sale_id": ids["sale_id"]},
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM public.sale_payment WHERE sale_id = :sale_id"),
                    {"sale_id": ids["sale_id"]},
                )
            ).scalar_one() == 1
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM public.prescription_log WHERE sale_id = :sale_id"),
                    {"sale_id": ids["sale_id"]},
                )
            ).scalar_one() == 1
    finally:
        await _cleanup(maintenance_engine, support_engine_sale_guard, ids)


async def test_app_role_can_edit_draft_and_finalize_exactly_once(
    app_engine_sale_guard: AsyncEngine,
    support_engine_sale_guard: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    ids = await _seed_finalized_sale(support_engine_sale_guard)
    draft_id = uuid4()
    try:
        async with app_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            await connection.execute(
                text(
                    "INSERT INTO public.sale ("
                    "id, tenant_id, branch_id, register_id, shift_id, cashier_user_id"
                    ") VALUES ("
                    ":id, :tenant_id, :branch_id, :register_id, :shift_id, :user_id"
                    ")"
                ),
                {**ids, "id": draft_id},
            )
            await connection.execute(
                text("UPDATE public.sale SET total_amount = 5 WHERE id = :id"),
                {"id": draft_id},
            )
            item_id = (
                await connection.execute(
                    text(
                        "INSERT INTO public.sale_item ("
                        "tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                        "total_price, position"
                        ") VALUES ("
                        ":tenant_id, :sale_id, :catalog_id, :batch_id, 1, 5, 5, 1"
                        ") RETURNING id"
                    ),
                    {
                        "tenant_id": ids["tenant_id"],
                        "sale_id": draft_id,
                        "catalog_id": ids["catalog_id"],
                        "batch_id": ids["batch_id"],
                    },
                )
            ).scalar_one()
            await connection.execute(
                text("UPDATE public.sale_item SET qty = 2, total_price = 10 WHERE id = :item_id"),
                {"item_id": item_id},
            )
            await connection.execute(
                text("DELETE FROM public.sale_item WHERE id = :item_id"),
                {"item_id": item_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_item ("
                    "tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                    "total_price, position"
                    ") VALUES ("
                    ":tenant_id, :sale_id, :catalog_id, :batch_id, 1, 5, 5, 1"
                    ")"
                ),
                {
                    "tenant_id": ids["tenant_id"],
                    "sale_id": draft_id,
                    "catalog_id": ids["catalog_id"],
                    "batch_id": ids["batch_id"],
                },
            )
            payment_id = (
                await connection.execute(
                    text(
                        "INSERT INTO public.sale_payment ("
                        "tenant_id, sale_id, payment_method, amount"
                        ") VALUES (:tenant_id, :sale_id, 'cash', 5) RETURNING id"
                    ),
                    {"tenant_id": ids["tenant_id"], "sale_id": draft_id},
                )
            ).scalar_one()
            await connection.execute(
                text("UPDATE public.sale_payment SET amount = 4 WHERE id = :payment_id"),
                {"payment_id": payment_id},
            )
            await connection.execute(
                text("DELETE FROM public.sale_payment WHERE id = :payment_id"),
                {"payment_id": payment_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_payment ("
                    "tenant_id, sale_id, payment_method, amount"
                    ") VALUES (:tenant_id, :sale_id, 'cash', 5)"
                ),
                {"tenant_id": ids["tenant_id"], "sale_id": draft_id},
            )
            missing_snapshot = await connection.begin_nested()
            with pytest.raises(
                DBAPIError,
                match="Completed sale requires an immutable receipt snapshot",
            ):
                await connection.execute(
                    text(
                        "UPDATE public.sale SET "
                        "status = 'completed', completed_at = now(), "
                        "receipt_number = 'GUARD-000002', receipt_seq = 2 "
                        "WHERE id = :id"
                    ),
                    {"id": draft_id},
                )
            await missing_snapshot.rollback()
            await connection.execute(
                text(
                    "UPDATE public.sale SET "
                    "status = 'completed', completed_at = now(), "
                    "receipt_snapshot = '{}'::jsonb, "
                    "receipt_number = 'GUARD-000002', receipt_seq = 2 "
                    "WHERE id = :id"
                ),
                {"id": draft_id},
            )

        await _expect_rejected(
            app_engine_sale_guard,
            ids["tenant_id"],
            "UPDATE public.sale SET total_amount = 6 WHERE id = :id",
            {"id": draft_id},
        )
    finally:
        await _cleanup(maintenance_engine, support_engine_sale_guard, ids)


async def test_draft_ownership_and_finalization_balances_are_enforced(
    app_engine_sale_guard: AsyncEngine,
    support_engine_sale_guard: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    ids = await _seed_finalized_sale(support_engine_sale_guard)
    draft_id = uuid4()
    item_id = uuid4()
    payment_id = uuid4()
    try:
        async with app_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            await connection.execute(
                text(
                    "INSERT INTO public.sale ("
                    "id, tenant_id, branch_id, register_id, shift_id, cashier_user_id, "
                    "total_amount"
                    ") VALUES ("
                    ":id, :tenant_id, :branch_id, :register_id, :shift_id, :user_id, 10"
                    ")"
                ),
                {**ids, "id": draft_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_item ("
                    "id, tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                    "total_price, position"
                    ") VALUES ("
                    ":id, :tenant_id, :sale_id, :catalog_id, :batch_id, 1, 10, 10, 1"
                    ")"
                ),
                {
                    "id": item_id,
                    "tenant_id": ids["tenant_id"],
                    "sale_id": draft_id,
                    "catalog_id": ids["catalog_id"],
                    "batch_id": ids["batch_id"],
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_payment ("
                    "id, tenant_id, sale_id, payment_method, amount"
                    ") VALUES (:id, :tenant_id, :sale_id, 'cash', 9)"
                ),
                {
                    "id": payment_id,
                    "tenant_id": ids["tenant_id"],
                    "sale_id": draft_id,
                },
            )

        await _expect_rejected(
            support_engine_sale_guard,
            ids["tenant_id"],
            "UPDATE public.sale SET tenant_id = :other_tenant_id WHERE id = :id",
            {"id": draft_id, "other_tenant_id": uuid4()},
        )
        await _expect_rejected(
            support_engine_sale_guard,
            ids["tenant_id"],
            "UPDATE public.sale_item SET sale_id = :other_sale_id WHERE id = :id",
            {"id": item_id, "other_sale_id": ids["sale_id"]},
        )
        await _expect_rejected(
            app_engine_sale_guard,
            ids["tenant_id"],
            "UPDATE public.sale SET "
            "status = 'completed', completed_at = now(), "
            "receipt_snapshot = '{}'::jsonb, "
            "receipt_number = 'GUARD-000002', receipt_seq = 2 "
            "WHERE id = :id",
            {"id": draft_id},
        )

        async with app_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            assert (
                await connection.execute(
                    text("SELECT status FROM public.sale WHERE id = :id"),
                    {"id": draft_id},
                )
            ).scalar_one() == "draft"
    finally:
        await _cleanup(maintenance_engine, support_engine_sale_guard, ids)


async def test_database_rejects_return_quantity_above_original_sale(
    app_engine_sale_guard: AsyncEngine,
    support_engine_sale_guard: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    ids = await _seed_finalized_sale(support_engine_sale_guard)
    return_id = uuid4()
    try:
        async with app_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            await connection.execute(
                text(
                    "INSERT INTO public.sale ("
                    "id, tenant_id, branch_id, register_id, shift_id, cashier_user_id, "
                    "sale_type, parent_sale_id, total_amount"
                    ") VALUES ("
                    ":id, :tenant_id, :branch_id, :register_id, :shift_id, :user_id, "
                    "'return', :parent_sale_id, 20"
                    ")"
                ),
                {**ids, "id": return_id, "parent_sale_id": ids["sale_id"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_item ("
                    "tenant_id, sale_id, parent_sale_item_id, catalog_id, batch_id, "
                    "qty, unit_price, total_price, position"
                    ") VALUES ("
                    ":tenant_id, :sale_id, :parent_item_id, :catalog_id, :batch_id, "
                    "2, 10, 20, 1"
                    ")"
                ),
                {
                    "tenant_id": ids["tenant_id"],
                    "sale_id": return_id,
                    "parent_item_id": ids["item_id"],
                    "catalog_id": ids["catalog_id"],
                    "batch_id": ids["batch_id"],
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_payment ("
                    "tenant_id, sale_id, payment_method, amount"
                    ") VALUES (:tenant_id, :sale_id, 'cash', 20)"
                ),
                {"tenant_id": ids["tenant_id"], "sale_id": return_id},
            )

        await _expect_rejected(
            app_engine_sale_guard,
            ids["tenant_id"],
            "UPDATE public.sale SET "
            "status = 'completed', completed_at = now(), "
            "receipt_snapshot = '{}'::jsonb, "
            "receipt_number = 'GUARD-000002', receipt_seq = 2 "
            "WHERE id = :id",
            {"id": return_id},
        )

        async with app_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            assert (
                await connection.execute(
                    text("SELECT status FROM public.sale WHERE id = :id"),
                    {"id": return_id},
                )
            ).scalar_one() == "draft"
    finally:
        await _cleanup(maintenance_engine, support_engine_sale_guard, ids)


async def test_late_component_insert_cannot_race_sale_finalization(
    app_engine_sale_guard: AsyncEngine,
    support_engine_sale_guard: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    ids = await _seed_finalized_sale(support_engine_sale_guard)
    draft_id = uuid4()
    try:
        async with support_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            await connection.execute(
                text(
                    "INSERT INTO public.sale ("
                    "id, tenant_id, branch_id, register_id, shift_id, cashier_user_id, "
                    "total_amount"
                    ") VALUES ("
                    ":id, :tenant_id, :branch_id, :register_id, :shift_id, :user_id, 10"
                    ")"
                ),
                {**ids, "id": draft_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_item ("
                    "tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                    "total_price, position"
                    ") VALUES ("
                    ":tenant_id, :sale_id, :catalog_id, :batch_id, 1, 10, 10, 1"
                    ")"
                ),
                {
                    "tenant_id": ids["tenant_id"],
                    "sale_id": draft_id,
                    "catalog_id": ids["catalog_id"],
                    "batch_id": ids["batch_id"],
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.sale_payment ("
                    "tenant_id, sale_id, payment_method, amount"
                    ") VALUES (:tenant_id, :sale_id, 'cash', 10)"
                ),
                {"tenant_id": ids["tenant_id"], "sale_id": draft_id},
            )

        insert_started = asyncio.Event()

        async def insert_late_item() -> str | None:
            try:
                async with app_engine_sale_guard.begin() as connection:
                    await _set_tenant(connection, ids["tenant_id"])
                    insert_started.set()
                    await connection.execute(
                        text(
                            "INSERT INTO public.sale_item ("
                            "tenant_id, sale_id, catalog_id, batch_id, qty, unit_price, "
                            "total_price, position"
                            ") VALUES ("
                            ":tenant_id, :sale_id, :catalog_id, :batch_id, 1, 10, 10, 2"
                            ")"
                        ),
                        {
                            "tenant_id": ids["tenant_id"],
                            "sale_id": draft_id,
                            "catalog_id": ids["catalog_id"],
                            "batch_id": ids["batch_id"],
                        },
                    )
            except DBAPIError as error:
                return getattr(error.orig, "sqlstate", None)
            return None

        async with app_engine_sale_guard.connect() as finalizer:
            transaction = await finalizer.begin()
            await _set_tenant(finalizer, ids["tenant_id"])
            await finalizer.execute(
                text(
                    "UPDATE public.sale SET "
                    "status = 'completed', completed_at = now(), "
                    "receipt_snapshot = '{}'::jsonb, "
                    "receipt_number = 'GUARD-000002', receipt_seq = 2 "
                    "WHERE id = :sale_id"
                ),
                {"sale_id": draft_id},
            )
            late_insert = asyncio.create_task(insert_late_item())
            await asyncio.wait_for(insert_started.wait(), timeout=2)
            await asyncio.sleep(0.1)
            assert not late_insert.done()
            await transaction.commit()

        assert await asyncio.wait_for(late_insert, timeout=2) == "23514"
        async with support_engine_sale_guard.begin() as connection:
            await _set_tenant(connection, ids["tenant_id"])
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM public.sale_item WHERE sale_id = :sale_id"),
                    {"sale_id": draft_id},
                )
            ).scalar_one() == 1
    finally:
        await _cleanup(maintenance_engine, support_engine_sale_guard, ids)
