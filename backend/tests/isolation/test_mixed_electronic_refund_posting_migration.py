"""Additive mixed posting guards reject partial commits and preserve historical contracts."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.mixed_electronic_refund_posting_repository import (
    MixedElectronicRefundPostingRepository,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_posting import facts, post, reconciled
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    mixed_electronic_refund_posting_migration,
    run_historical_rehearsal,
)

db_session = receiving_session_fixture
TABLE = "pos_mixed_electronic_refund_posting"


def test_generated_posting_sql_has_complete_private_functions_and_reversible_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = mixed_electronic_refund_posting_migration()
    statements: list[str] = []
    patches: list[tuple[str, str, str, bool]] = []

    def capture(signature: str, old: str, new: str, *, upgrade: bool) -> None:
        patches.append((signature, old, new, upgrade))

    monkeypatch.setattr(migration, "_sql", statements.append)
    monkeypatch.setattr(migration, "_patch", capture)
    migration.upgrade()
    creates = "\n".join(statements)
    assert migration.SQL.count("CREATE FUNCTION public.") == 4
    assert migration.SQL.count("$$") == 8
    assert migration.SQL.count("END $$;") == 4
    assert "END $;" not in migration.SQL
    assert "IF TG_TABLE_NAME='sale' THEN\n  target_sale:=NEW.id;" in migration.SQL
    marker = migration.SQL.split(
        "CREATE FUNCTION public.trg_mixed_electronic_refund_posting_guard", 1
    )[1]
    assert marker.index("mixed-electronic-refund-review:") < marker.index("aurum-mixed-return:")
    assert marker.index("aurum-mixed-return:") < marker.index("SELECT * INTO p")
    assert marker.index("transaction_isolation") < marker.index("SELECT * INTO p")
    assert creates.count("CREATE CONSTRAINT TRIGGER mixed_electronic_refund_posting_complete") == 4
    expected = {
        "trg_mixed_electronic_refund_binding_fence()",
        "trg_unit_payment_binding_fence()",
        "trg_unit_cart_payment_boundary()",
        "trg_mixed_checkout_return_boundary()",
        "trg_guard_sale_immutability()",
        "mixed_electronic_refund_binding_active(uuid,uuid)",
        "trg_unit_refund_payment_component_guard()",
        "trg_unit_refund_payment_component_complete()",
        "audit_redact_table_snapshot(text,jsonb)",
    }
    assert {signature for signature, _, _, upgrade in patches if upgrade} == expected
    applied = list(patches)
    statements.clear()
    migration.downgrade()
    assert patches[len(applied) :] == [
        (signature, old, new, False) for signature, old, new, _ in reversed(applied)
    ]
    drops = "\n".join(statements)
    assert drops.index("downgrade_requires_empty_history") < drops.index("DROP COLUMN")
    for table in ("sale", "sale_payment", "pos_unit_refund_payment_component"):
        assert f"ON public.{table};" in drops
    for forbidden in (
        "fiscal_data",
        "marking_codes",
        "voided_at",
        "voided_by_sale_id",
        "pos_unit_cart_item",
        "prescription_log",
        "batch_movement",
        "unit_stock_cart_command_v2",
    ):
        assert forbidden in migration.SQL


async def test_posting_roundtrip_restores_exact_head_functions_security_and_component_parent(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        migration = mixed_electronic_refund_posting_migration()
        definitions = text(
            "SELECT pg_get_functiondef(oid),proowner,proacl::text,prosecdef,proconfig "
            "FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"
        )
        before = {
            signature: connection.execute(definitions, {"signature": f"public.{signature}"}).one()
            for signature, _, _ in migration.PATCHES
        }
        security = text(
            "SELECT relowner,relacl::text,relrowsecurity,relforcerowsecurity FROM pg_class "
            f"WHERE oid='public.{TABLE}'::regclass"
        )
        head_security = connection.execute(security).one()
        column = text(
            "SELECT attname,atttypid::regtype::text,attnotnull,attacl::text FROM pg_attribute "
            "WHERE attrelid='public.pos_unit_refund_payment_component'::regclass "
            "AND attname='mixed_electronic_refund_id' AND NOT attisdropped"
        )
        head_column = connection.execute(column).all()
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM public.{TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                    with connection.begin_nested():
                        migration.downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
                return
            migration.downgrade()
            assert connection.scalar(text("SELECT to_regclass(:table)"), {"table": TABLE}) is None
            assert connection.execute(column).all() == []
            downgraded = {
                signature: connection.execute(
                    definitions, {"signature": f"public.{signature}"}
                ).one()
                for signature, _, _ in migration.PATCHES
            }
            assert all(before[name][0] != downgraded[name][0] for name in before)
            assert all(before[name][1:] == downgraded[name][1:] for name in before)
            migration.upgrade()
        assert connection.execute(security).one() == head_security
        assert connection.execute(column).all() == head_column
        assert {
            signature: connection.execute(definitions, {"signature": f"public.{signature}"}).one()
            for signature, _, _ in migration.PATCHES
        } == before
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity "
                "AND relowner='aurum_schema_owner'::regrole FROM pg_class "
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
            for signature in migration.FUNCTIONS:
                assert not connection.scalar(
                    text("SELECT has_function_privilege(:role,:function,'EXECUTE')"),
                    {"role": role, "function": f"public.{signature}"},
                )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=:table"
                ),
                {"table": TABLE},
            )
            == 3
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(run_historical_rehearsal, rehearse)
        finally:
            await transaction.rollback()


async def raw_marker(
    session: AsyncSession, operation_id: UUID, parent_sale_id: UUID, shift_id: UUID
) -> UUID:
    sale_id = uuid4()
    inserted = await session.scalar(
        text(
            f"INSERT INTO {TABLE} "
            "(tenant_id,id,parent_sale_id,sale_id,branch_id,register_id,shift_id,"
            "actor_id,created_by) "
            "SELECT tenant_id,id,parent_sale_id,:sale,branch_id,register_id,"
            ":shift,actor_id,actor_id "
            "FROM pos_mixed_electronic_refund_activation "
            "WHERE tenant_id=current_tenant_id() AND id=:operation "
            "AND parent_sale_id=:parent RETURNING sale_id"
        ),
        {"operation": operation_id, "parent": parent_sale_id, "sale": sale_id, "shift": shift_id},
    )
    assert inserted == sale_id
    return sale_id


async def test_raw_marker_without_financial_rows_cannot_satisfy_deferred_completion(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    before = await facts(db_session)
    with pytest.raises(DBAPIError, match="mixed_electronic_refund_posting_incomplete") as denied:
        async with db_session.begin_nested():
            await raw_marker(
                db_session, request.operation_id, request.request.parent_sale_id, shift_id
            )
            await db_session.execute(
                text("SET CONSTRAINTS mixed_electronic_refund_posting_complete IMMEDIATE")
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert await facts(db_session) == before


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
async def test_posted_marker_is_immutable_even_through_raw_sql(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, verb: str
) -> None:
    request, shift_id = await reconciled(db_session)
    await post(db_session, request, shift_id)
    await db_session.commit()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    sql = (
        f"UPDATE {TABLE} SET reason_code=reason_code WHERE id=:id"
        if verb == "UPDATE"
        else f"DELETE FROM {TABLE} WHERE id=:id"
    )
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(sql), {"id": request.operation_id})
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
            marker_query = text(f"SELECT to_jsonb(row) FROM public.{TABLE} row WHERE id=:id")
            parameters = {"id": request.operation_id}
            marker_before = await connection.scalar(marker_query, parameters)
            assert marker_before is not None
            with pytest.raises(
                DBAPIError, match="mixed_electronic_refund_posting_immutable"
            ) as immutable:
                async with connection.begin_nested():
                    await connection.execute(text(sql), parameters)
            assert getattr(immutable.value.orig, "sqlstate", None) == "55000"
            assert await connection.scalar(marker_query, parameters) == marker_before
        finally:
            await transaction.rollback()
    assert await facts(db_session) == before


async def test_deferred_sale_callback_rechecks_current_actor_and_rolls_back_all_facts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, shift_id = await reconciled(db_session)
    context = runtime(db_session)
    before = await facts(db_session)

    async def defer(repository: MixedElectronicRefundPostingRepository) -> None:
        assert repository.session is db_session

    with monkeypatch.context() as patch:
        patch.setattr(MixedElectronicRefundPostingRepository, "validate_completion", defer)
        with pytest.raises(
            DBAPIError, match="mixed_electronic_refund_posting_actor_denied"
        ) as denied:
            async with db_session.begin_nested():
                await post(db_session, request, shift_id)
                await db_session.execute(
                    text("SELECT set_config('app.user_id',:actor,true)"), {"actor": str(uuid4())}
                )
                await db_session.execute(
                    text("SET CONSTRAINTS mixed_electronic_refund_posting_complete IMMEDIATE")
                )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_nonempty_posting_downgrade_refuses_without_mutating_history(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    request, shift_id = await reconciled(db_session)
    await post(db_session, request, shift_id)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        query = text(f"SELECT to_jsonb(row) FROM public.{TABLE} row WHERE id=:id")
        before = connection.scalar(query, {"id": request.operation_id})
        assert before is not None
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                with connection.begin_nested():
                    mixed_electronic_refund_posting_migration().downgrade()
        assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert connection.scalar(query, {"id": request.operation_id}) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(refuse)
        finally:
            await transaction.rollback()


async def wait_for_blocker(observer: AsyncSession, waiting: int, holder: int) -> None:
    async with asyncio.timeout(10):
        while not await observer.scalar(
            text("SELECT :holder = ANY(pg_blocking_pids(:waiting))"),
            {"holder": holder, "waiting": waiting},
        ):
            await asyncio.sleep(0)


async def test_raw_marker_waits_for_journal_operation_before_parent_without_deadlock(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    await db_session.scalar(
        text("SELECT id FROM sale WHERE tenant_id=:tenant AND id=:parent FOR UPDATE"),
        {"tenant": context.stock.tenant_id, "parent": request.request.parent_sale_id},
    )
    parent_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(parent_pid, int)
    journal_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    posting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def journal_contender() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int) and pid != parent_pid
            journal_pid.set_result(pid)
            with pytest.raises(
                DBAPIError, match="mixed_electronic_refund_binding_parent_frozen"
            ) as denied:
                await session.execute(
                    text(
                        "INSERT INTO pos_mixed_return_command "
                        "(tenant_id,id,parent_sale_id,parent_checkout_id,stock_command_id,"
                        "branch_id,actor_id,request_hash,state,total_amount,physical_cost_amount,"
                        "result_payload,created_by,updated_by) "
                        "SELECT tenant_id,:operation,sale_id,id,stock_command_id,branch_id,"
                        ":actor,repeat('a',64),'prepared_quarantine',0,0,'{}'::jsonb,:actor,:actor "
                        "FROM pos_mixed_checkout WHERE tenant_id=:tenant AND sale_id=:parent"
                    ),
                    {
                        "tenant": context.stock.tenant_id,
                        "operation": request.operation_id,
                        "actor": context.actor_id,
                        "parent": request.request.parent_sale_id,
                    },
                )
            assert getattr(denied.value.orig, "sqlstate", None) == "23514"
            await session.rollback()

    async def posting_contender() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int) and pid != parent_pid
            posting_pid.set_result(pid)
            await raw_marker(
                session, request.operation_id, request.request.parent_sale_id, shift_id
            )
            await session.rollback()

    journal_task = asyncio.create_task(journal_contender())
    posting_task: asyncio.Task[None] | None = None
    try:
        first = await asyncio.wait_for(journal_pid, timeout=5)
        await wait_for_blocker(db_session, first, parent_pid)
        posting_task = asyncio.create_task(posting_contender())
        second = await asyncio.wait_for(posting_pid, timeout=5)
        assert second != first
        await wait_for_blocker(db_session, second, first)
        await db_session.commit()
        await asyncio.wait_for(journal_task, timeout=10)
        await asyncio.wait_for(posting_task, timeout=10)
    finally:
        await db_session.rollback()
        tasks = [journal_task] + ([posting_task] if posting_task is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize(
    "field,expression",
    [
        ("fiscal_data", "'{}'::jsonb"),
        ("marking_codes", "'{}'::jsonb"),
        ("voided_at", "clock_timestamp()"),
        ("voided_by_sale_id", "gen_random_uuid()"),
    ],
)
async def test_raw_posting_allow_path_rejects_unrelated_sale_metadata(
    db_session: AsyncSession,
    field: str,
    expression: str,
) -> None:
    request, shift_id = await reconciled(db_session)
    before = await facts(db_session)
    with pytest.raises(DBAPIError, match="mixed_electronic_refund_binding_parent_frozen") as denied:
        async with db_session.begin_nested():
            sale_id = await raw_marker(
                db_session, request.operation_id, request.request.parent_sale_id, shift_id
            )
            await db_session.execute(
                text(
                    "INSERT INTO sale "
                    "(id,tenant_id,branch_id,register_id,shift_id,cashier_user_id,sale_type,"
                    "parent_sale_id,status,is_test,total_amount,currency,"
                    f"operation_id,operation_hash,{field}) "
                    "SELECT :sale,m.tenant_id,m.branch_id,m.register_id,m.shift_id,m.actor_id,"
                    "'return',m.parent_sale_id,'draft',false,"
                    "(i.result_payload->>'total_amount')::numeric,'TJS',m.id,"
                    f"i.request_payload->>'expected_revision_hash',{expression} "
                    f"FROM {TABLE} m JOIN pos_mixed_electronic_refund_intent i "
                    "ON i.tenant_id=m.tenant_id AND i.id=m.id WHERE m.id=:operation"
                ),
                {"sale": sale_id, "operation": request.operation_id},
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert await facts(db_session) == before


async def test_concurrent_same_posting_recovers_one_exact_sale_and_payment_set(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.mixed_electronic_refund_posting_service import (
        MixedElectronicRefundPostingService,
    )

    request, shift_id = await reconciled(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    expected = await post(db_session, request, shift_id)
    holder = await db_session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(holder, int)
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contender() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int) and pid != holder
            waiting_pid.set_result(pid)
            result = await MixedElectronicRefundPostingService(
                MixedElectronicRefundPostingRepository(session)
            ).post(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                parent_sale_id=request.request.parent_sale_id,
                operation_id=request.operation_id,
                shift_id=shift_id,
            )
            assert result == expected
            await session.commit()

    task = asyncio.create_task(contender())
    try:
        waiting = await asyncio.wait_for(waiting_pid, timeout=5)
        await wait_for_blocker(db_session, waiting, holder)
        await db_session.commit()
        await asyncio.wait_for(task, timeout=10)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text(f"SELECT count(*) FROM {TABLE} WHERE id=:operation"),
            {"operation": request.operation_id},
        )
        == 1
    )
    ids = (
        (
            await db_session.execute(
                text("SELECT id FROM sale_payment WHERE tenant_id=:tenant AND sale_id=:sale"),
                {"tenant": context.stock.tenant_id, "sale": expected.sale_id},
            )
        )
        .scalars()
        .all()
    )
    assert set(ids) == {part.id for part in expected.receipt.payments}


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("different", [False, True])
async def test_parenthesized_mixed_allocation_case_parses_and_preserves_truth_table(
    maintenance_engine: AsyncEngine,
    kind: str,
    different: bool,
) -> None:
    assert kind in {"legacy_pack", "physical_unit"}
    quantity = "0.500" if kind == "legacy_pack" else "2"
    offset = "0.250" if kind == "legacy_pack" else "1"
    if different:
        quantity = "0.501" if kind == "legacy_pack" else "3"
    statement = text(
        "DO $$ DECLARE kind text := '" + kind + "'; "
        "legacy_quantity numeric := 0.500; legacy_offset numeric := 0.250; "
        "quantity integer := 2; item_offset integer := 1; BEGIN "
        "IF " + quantity + " IS DISTINCT FROM "
        "(CASE WHEN kind='legacy_pack' THEN legacy_quantity ELSE quantity END) "
        "OR " + offset + " IS DISTINCT FROM "
        "(CASE WHEN kind='legacy_pack' THEN legacy_offset ELSE item_offset END) THEN "
        "RAISE EXCEPTION 'mixed_allocation_predicate_differs'; END IF; END $$;"
    )
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            if different:
                with pytest.raises(
                    DBAPIError, match="mixed_allocation_predicate_differs"
                ) as denied:
                    async with connection.begin_nested():
                        await connection.execute(statement)
                assert getattr(denied.value.orig, "sqlstate", None) == "P0001"
            else:
                await connection.execute(statement)
        finally:
            await transaction.rollback()
