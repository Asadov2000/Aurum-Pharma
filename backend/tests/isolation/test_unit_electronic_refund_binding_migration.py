"""Binding history and raw SQL fences on an isolated disposable test database."""

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import (
    SaveElectronicRefundIntentRequest,
)
from app.domains.pos.unit_electronic_refund_intent_service import (
    UnitElectronicRefundIntentService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_unit_electronic_refund_intent import save_review
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    downgrade_mixed_for_physical_refund_roundtrip,
    electronic_refund_activation_migration,
    electronic_refund_binding_migration,
    electronic_refund_outcome_migration,
    electronic_refund_posting_migration,
    mixed_electronic_checkout_migration,
    mixed_electronic_refund_intent_migration,
    mixed_electronic_refund_outcome_migration,
    mixed_electronic_refund_posting_migration,
    mixed_electronic_refund_preparation_migration,
    run_historical_rehearsal,
)

db_session = receiving_session_fixture
TABLES = ("pos_unit_electronic_refund_binding", "pos_unit_electronic_refund_binding_cancel")


async def saved_review(session: AsyncSession) -> SaveElectronicRefundIntentRequest:
    request = await save_review(session)
    await persist_review(session, request)
    return request


async def persist_review(session: AsyncSession, request: SaveElectronicRefundIntentRequest) -> None:
    context = runtime(session)
    actor = await session.scalar(text("SELECT public.current_app_user_id()"))
    assert isinstance(actor, UUID)
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=actor,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def insert_history(
    session: AsyncSession, operation: UUID, *, cancel: bool = False
) -> datetime:
    table = TABLES[int(cancel)]
    result = await session.scalar(
        text(
            f"INSERT INTO {table} (tenant_id,id,parent_sale_id,branch_id,actor_id,created_by) "
            "SELECT tenant_id,id,parent_sale_id,branch_id,actor_id,actor_id "
            "FROM pos_unit_electronic_refund_intent WHERE id=:operation "
            "RETURNING created_at"
        ),
        {"operation": operation},
    )
    assert isinstance(result, datetime)
    return result


async def test_binding_roundtrip_preserves_security(maintenance_engine: AsyncEngine) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        snapshot = text(
            "SELECT relname,relowner,relacl::text,relrowsecurity,relforcerowsecurity "
            "FROM pg_class WHERE relnamespace='public'::regnamespace "
            "AND relname IN ('pos_unit_electronic_refund_binding',"
            "'pos_unit_electronic_refund_binding_cancel') ORDER BY relname"
        )
        before = connection.execute(snapshot).all()
        with Operations.context(MigrationContext.configure(connection)):
            if any(
                connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {table})")) for table in TABLES
            ):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        electronic_refund_binding_migration().downgrade()
                return
            if not downgrade_mixed_for_physical_refund_roundtrip(connection):
                return
            electronic_refund_posting_migration().downgrade()
            electronic_refund_outcome_migration().downgrade()
            electronic_refund_activation_migration().downgrade()
            electronic_refund_binding_migration().downgrade()
            for table in TABLES:
                assert connection.scalar(text(f"SELECT to_regclass('public.{table}')")) is None
            electronic_refund_binding_migration().upgrade()
            electronic_refund_activation_migration().upgrade()
            electronic_refund_outcome_migration().upgrade()
            electronic_refund_posting_migration().upgrade()
            mixed_electronic_checkout_migration().upgrade()
            mixed_electronic_refund_intent_migration().upgrade()
            mixed_electronic_refund_preparation_migration().upgrade()
            mixed_electronic_refund_outcome_migration().upgrade()
            mixed_electronic_refund_posting_migration().upgrade()
        assert connection.execute(snapshot).all() == before
        for table in TABLES:
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity AND "
                    "relowner='aurum_schema_owner'::regrole FROM pg_class "
                    f"WHERE oid='public.{table}'::regclass"
                )
            )
            policies = connection.execute(
                text(
                    "SELECT policyname,qual,with_check FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=:table ORDER BY policyname"
                ),
                {"table": table},
            ).all()
            assert [row.policyname for row in policies] == [
                "schema_owner",
                "support",
                "tenant_actor",
            ]
            assert "actor_id = current_app_user_id()" in policies[2].qual
            assert "pos.refund" in policies[2].qual
            assert "pos.refund" in policies[2].with_check
            for role, privileges in (
                ("aurum_app", {"SELECT", "INSERT"}),
                ("aurum_support", {"SELECT"}),
            ):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert connection.scalar(
                        text("SELECT has_table_privilege(:role,:table,:privilege)"),
                        {"role": role, "table": f"public.{table}", "privilege": privilege},
                    ) is (privilege in privileges)
        for signature in electronic_refund_binding_migration().FUNCTIONS:
            definition = connection.scalar(
                text("SELECT pg_get_functiondef(CAST(:function AS regprocedure))"),
                {"function": f"public.{signature}"},
            )
            assert "SECURITY DEFINER" in definition
            assert "SET search_path TO 'pg_catalog', 'pg_temp'" in definition
            for role in ("aurum_app", "aurum_support"):
                assert (
                    connection.scalar(
                        text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                        {"role": role, "function": f"public.{signature}"},
                    )
                    is False
                )
        fences = (
            connection.execute(
                text(
                    "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "WHERE t.tgname='a01_unit_electronic_refund_binding_fence' ORDER BY c.relname"
                )
            )
            .scalars()
            .all()
        )
        assert fences == ["pos_physical_return_command", "pos_refund_attempt", "sale"]

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("cancel_first", [False, True])
async def test_binding_cancel_tombstone_never_pays_or_reactivates(
    db_session: AsyncSession, cancel_first: bool
) -> None:
    request = await saved_review(db_session)
    before = await facts(db_session)
    bound_at = None if cancel_first else await insert_history(db_session, request.operation_id)
    cancelled_at = await insert_history(db_session, request.operation_id, cancel=True)
    assert bound_at is None or cancelled_at >= bound_at
    with pytest.raises(DBAPIError, match="unit_electronic_refund_binding_cancelled"):
        async with db_session.begin_nested():
            await insert_history(db_session, request.operation_id)
    assert await facts(db_session) == before
    audits = list(
        (
            await db_session.execute(
                text(
                    "SELECT table_name,new_values FROM audit_log WHERE record_id=:id "
                    "AND table_name IN ('pos_unit_electronic_refund_binding',"
                    "'pos_unit_electronic_refund_binding_cancel') ORDER BY table_name"
                ),
                {"id": request.operation_id},
            )
        ).all()
    )
    assert len(audits) == (1 if cancel_first else 2)
    for row in audits:
        assert not ({"request_payload", "result_payload"} & row.new_values.keys())
    # A fresh operation can bind after cancellation; this one remains permanently cancelled.
    fresh = request.model_copy(update={"operation_id": uuid4()})
    await persist_review(db_session, fresh)
    await insert_history(db_session, fresh.operation_id)


async def test_other_author_cannot_bind_while_hidden_original_binding_active(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    assert not await db_session.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLES[0]})"))
    fresh = request.model_copy(update={"operation_id": uuid4()})
    await persist_review(db_session, fresh)
    with pytest.raises(DBAPIError, match="unit_electronic_refund_binding_already_active"):
        async with db_session.begin_nested():
            await insert_history(db_session, fresh.operation_id)


@pytest.mark.parametrize("table", ["sale", "pos_physical_return_command", "pos_refund_attempt"])
async def test_raw_competing_return_inserts_hit_fence_before_other_guards(
    db_session: AsyncSession, table: str
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id)
    context = runtime(db_session)
    before = await facts(db_session)
    # Intentionally minimal raw rows prove that the early fence protects every entry path.
    # BEFORE triggers run before NOT NULL checks and the later domain-specific guards.
    with pytest.raises(DBAPIError, match="unit_electronic_refund_binding_parent_frozen"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {table} (tenant_id,id,parent_sale_id) "
                    "VALUES (:tenant,:id,:parent)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": uuid4(),
                    "parent": request.request.parent_sale_id,
                },
            )
    assert await facts(db_session) == before


async def test_binding_scope_forgery_cannot_cross_review_parent(db_session: AsyncSession) -> None:
    request = await saved_review(db_session)
    with pytest.raises(DBAPIError, match="unit_electronic_refund_binding_scope_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLES[0]} "
                    "(tenant_id,id,parent_sale_id,branch_id,actor_id,created_by) "
                    "SELECT tenant_id,id,:parent,branch_id,actor_id,actor_id "
                    "FROM pos_unit_electronic_refund_intent WHERE id=:operation"
                ),
                {"parent": uuid4(), "operation": request.operation_id},
            )


@pytest.mark.parametrize("cancel_only", [False, True])
async def test_committed_binding_or_cancel_history_refuses_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, cancel_only: bool
) -> None:
    request = await saved_review(db_session)
    await insert_history(db_session, request.operation_id, cancel=cancel_only)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        snapshot = text(
            "SELECT id,created_at,created_by FROM pos_unit_electronic_refund_binding "
            "UNION ALL SELECT id,created_at,created_by "
            "FROM pos_unit_electronic_refund_binding_cancel "
            "ORDER BY id,created_at"
        )
        before = connection.execute(snapshot).all()
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                with connection.begin_nested():
                    electronic_refund_binding_migration().downgrade()
        assert connection.execute(snapshot).all() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()
