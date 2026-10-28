"""Durable activation is scoped, immutable and cannot be cancelled or downgraded."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_mixed_for_physical_refund_roundtrip,
    electronic_refund_activation_migration,
    electronic_refund_outcome_migration,
    electronic_refund_posting_migration,
    mixed_electronic_checkout_migration,
    mixed_electronic_refund_intent_migration,
    mixed_electronic_refund_outcome_migration,
    mixed_electronic_refund_posting_migration,
    mixed_electronic_refund_preparation_migration,
    run_historical_rehearsal,
)
from tests.isolation.test_unit_electronic_refund_binding_migration import (
    insert_history,
    saved_review,
)

db_session = receiving_session_fixture
TABLE = "pos_unit_electronic_refund_activation"


async def activate(
    session: AsyncSession, operation: UUID, *, shift_id: UUID | None = None
) -> datetime:
    result = await session.scalar(
        text(
            f"INSERT INTO {TABLE} "
            "(tenant_id,id,parent_sale_id,branch_id,actor_id,register_id,shift_id,created_by) "
            "SELECT b.tenant_id,b.id,b.parent_sale_id,b.branch_id,b.actor_id,s.register_id,"
            "coalesce(CAST(:shift AS uuid),s.shift_id),b.actor_id "
            "FROM pos_unit_electronic_refund_binding b JOIN sale s "
            "ON s.tenant_id=b.tenant_id AND s.id=b.parent_sale_id "
            "WHERE b.id=:id RETURNING created_at"
        ),
        {"id": operation, "shift": shift_id},
    )
    assert isinstance(result, datetime)
    return result


async def test_activation_roundtrip_restores_binding_guard(maintenance_engine: AsyncEngine) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        function = text(
            "SELECT pg_get_functiondef("
            "'public.trg_unit_electronic_refund_binding_guard()'::regprocedure)"
        )
        before = connection.scalar(function)
        snapshot = text(
            f"SELECT relowner,relacl::text,relrowsecurity,relforcerowsecurity FROM pg_class "
            f"WHERE oid='public.{TABLE}'::regclass"
        )
        security = connection.execute(snapshot).one()
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        electronic_refund_activation_migration().downgrade()
                return
            if not downgrade_mixed_for_physical_refund_roundtrip(connection):
                return
            electronic_refund_posting_migration().downgrade()
            electronic_refund_outcome_migration().downgrade()
            electronic_refund_activation_migration().downgrade()
            assert "activation_cancel_denied" not in connection.scalar(function)
            electronic_refund_activation_migration().upgrade()
            electronic_refund_outcome_migration().upgrade()
            electronic_refund_posting_migration().upgrade()
            mixed_electronic_checkout_migration().upgrade()
            mixed_electronic_refund_intent_migration().upgrade()
            mixed_electronic_refund_preparation_migration().upgrade()
            mixed_electronic_refund_outcome_migration().upgrade()
            mixed_electronic_refund_posting_migration().upgrade()
        assert connection.scalar(function) == before
        assert connection.execute(snapshot).one() == security
        assert security.relrowsecurity and security.relforcerowsecurity
        assert connection.scalar(
            text(
                "SELECT relowner='aurum_schema_owner'::regrole FROM pg_class "
                f"WHERE oid='public.{TABLE}'::regclass"
            )
        )
        for role, allowed in (("aurum_app", {"SELECT", "INSERT"}), ("aurum_support", {"SELECT"})):
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert connection.scalar(
                    text("SELECT has_table_privilege(:role,:table,:privilege)"),
                    {"role": role, "table": f"public.{TABLE}", "privilege": privilege},
                ) is (privilege in allowed)
            assert not connection.scalar(
                text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                {"role": role, "signature": "public.trg_unit_electronic_refund_activation_guard()"},
            )
        definition = connection.scalar(
            text(
                "SELECT pg_get_functiondef("
                "'public.trg_unit_electronic_refund_activation_guard()'::regprocedure)"
            )
        )
        assert "SECURITY DEFINER" in definition
        assert "SET search_path TO 'pg_catalog', 'pg_temp'" in definition
        policies = connection.execute(
            text(
                "SELECT policyname,qual,with_check FROM pg_policies "
                f"WHERE tablename='{TABLE}' ORDER BY policyname"
            )
        ).all()
        assert [row.policyname for row in policies] == ["schema_owner", "support", "tenant_actor"]
        assert "actor_id = current_app_user_id()" in policies[2].qual
        assert "pos.refund" in policies[2].qual and "pos.refund" in policies[2].with_check

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


async def test_activation_blocks_raw_cancel_and_keeps_financial_facts(
    db_session: AsyncSession,
) -> None:
    request = await saved_review(db_session)
    bound_at = await insert_history(db_session, request.operation_id)
    before = await facts(db_session)
    assert await activate(db_session, request.operation_id) >= bound_at
    with pytest.raises(DBAPIError, match="unit_electronic_refund_activation_cancel_denied"):
        async with db_session.begin_nested():
            await insert_history(db_session, request.operation_id, cancel=True)
    assert await facts(db_session) == before
    audit = await db_session.scalar(
        text(f"SELECT new_values FROM audit_log WHERE table_name='{TABLE}' AND record_id=:id"),
        {"id": request.operation_id},
    )
    assert audit and not ({"request_payload", "result_payload"} & audit.keys())


async def test_cancelled_binding_cannot_activate(db_session: AsyncSession) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    await insert_history(db_session, request.operation_id, cancel=True)
    with pytest.raises(DBAPIError, match="unit_electronic_refund_activation_binding_invalid"):
        async with db_session.begin_nested():
            await activate(db_session, request.operation_id)


@pytest.mark.parametrize("unknown_shift", [True, False])
async def test_activation_requires_actual_open_shift(
    db_session: AsyncSession, unknown_shift: bool
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    shift_id = await db_session.scalar(
        text("SELECT shift_id FROM sale WHERE id=:id"), {"id": request.request.parent_sale_id}
    )
    assert isinstance(shift_id, UUID)
    if not unknown_shift:
        await close_shift(db_session, shift_id)
    with pytest.raises(DBAPIError, match="unit_electronic_refund_activation_shift_invalid"):
        async with db_session.begin_nested():
            await activate(
                db_session, request.operation_id, shift_id=uuid4() if unknown_shift else shift_id
            )


async def test_activation_hidden_from_other_author(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    await activate(db_session, request.operation_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    assert not await db_session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})"))


async def test_activation_history_prevents_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    await activate(db_session, request.operation_id)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                with connection.begin_nested():
                    electronic_refund_activation_migration().downgrade()
        assert connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})"))

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("column", ["parent_sale_id", "register_id", "branch_id", "actor_id"])
async def test_raw_activation_rejects_scope_forgery(db_session: AsyncSession, column: str) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    fields = {
        "parent_sale_id": "b.parent_sale_id",
        "register_id": "s.register_id",
        "branch_id": "b.branch_id",
        "actor_id": "b.actor_id",
    }
    fields[column] = "CAST(:wrong AS uuid)"
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(tenant_id,id,parent_sale_id,register_id,branch_id,actor_id,"
                    "shift_id,created_by) "
                    f"SELECT b.tenant_id,b.id,{fields['parent_sale_id']},"
                    f"{fields['register_id']},{fields['branch_id']},{fields['actor_id']},"
                    "s.shift_id,b.actor_id FROM pos_unit_electronic_refund_binding b JOIN sale s "
                    "ON s.tenant_id=b.tenant_id AND s.id=b.parent_sale_id WHERE b.id=:id"
                ),
                {"id": request.operation_id, "wrong": uuid4()},
            )
    assert not await db_session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})"))


async def test_readonly_activation_read_is_allowed_but_insert_is_denied(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    await activate(db_session, request.operation_id)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await db_session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})"))
        # Even replaying an existing row must reach the early readonly write guard.
        with pytest.raises(DBAPIError, match="tenant_access_restricted"):
            async with db_session.begin_nested():
                await activate(db_session, request.operation_id)
    finally:
        await db_session.rollback()
