"""Mixed disposition writes enforce real identities and preserve immutable source facts."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.customer_returns.mixed_disposition import prepare_mixed_disposition
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService
from app.domains.customer_returns.mixed_repository import MixedCustomerReturnsRepository
from app.domains.customer_returns.mixed_schemas import MixedCustomerReturnRead
from app.domains.customer_returns.mixed_service import MixedCustomerReturnsService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.customer_returns.test_mixed_disposition import request
from tests.domains.customer_returns.test_mixed_disposition import source as contract_source
from tests.domains.customer_returns.test_mixed_queue import cash_source
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.inventory.test_unit_retail_ledger_v2 import retail_source
from tests.domains.pos.test_mixed_cash_checkout import complete
from tests.domains.pos.test_mixed_cash_refund import refund
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_mixed_return_journal import MixedSource, mixed_source, receive
from tests.domains.pos.test_mixed_zero_return import facts, intake
from tests.domains.pos.test_mixed_zero_return import source as zero_source
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
WRITER = text(
    """
 SELECT public.record_mixed_customer_return_disposition(
   :tenant,:decision,:intake,:line,:kind,:batch,:lot,:disposition,:reason,:comment)
"""
)


def migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    path = directory / "0193_mixed_quarantine_disposition.py"
    spec = importlib.util.spec_from_file_location("mixed_disposition_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def queue(session: AsyncSession, tenant_id: UUID) -> list[MixedCustomerReturnRead]:
    service = MixedCustomerReturnsService(MixedCustomerReturnsRepository(session))
    result = await service.list_returns(
        tenant_id=tenant_id,
        branch_id=None,
        search=None,
        allowed_branch_ids=None,
        page=1,
        page_size=200,
    )
    return result.items


async def confirmed(
    session: AsyncSession, *, intake_kind: Literal["cash_refund", "zero_return"] = "cash_refund"
) -> list[MixedCustomerReturnRead]:
    if intake_kind == "cash_refund":
        _, cash = await cash_source(session)
        operation_id = cash.operation_id
    else:
        zero = await intake(session, await zero_source(session))
        operation_id = zero.operation_id
    rows = await queue(session, runtime(session).stock.tenant_id)
    return [row for row in rows if row.operation_id == operation_id]


def parameters(
    row: MixedCustomerReturnRead,
    tenant_id: UUID,
    *,
    operation_id: UUID | None = None,
    comment: str | None = None,
) -> dict[str, object]:
    return {
        "tenant": tenant_id,
        "decision": operation_id or uuid4(),
        "intake": row.operation_id,
        "line": row.line_id,
        "kind": row.inventory_kind,
        "batch": row.batch_id,
        "lot": row.lot_id,
        "disposition": "disposed",
        "reason": "damaged",
        "comment": comment,
    }


async def record(session: AsyncSession, payload: dict[str, object]) -> dict[str, object]:
    result = await session.scalar(WRITER, payload)
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


async def unchanged_facts(session: AsyncSession) -> dict[str, list[object]]:
    # The decision adds its own audit event; all financial and stock facts stay exact.
    return {key: value for key, value in (await facts(session)).items() if key != "audit_log"}


@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_writer_derives_whole_confirmed_target_and_removes_only_it_from_pending(
    db_session: AsyncSession,
    intake_kind: Literal["cash_refund", "zero_return"],
    kind: Literal["legacy_pack", "physical_unit"],
) -> None:
    rows = await confirmed(db_session, intake_kind=intake_kind)
    assert len(rows) == 2
    row = next(row for row in rows if row.inventory_kind == kind)
    tenant_id = runtime(db_session).stock.tenant_id
    payload = parameters(row, tenant_id, comment="\u2003 Проверка решения \t")
    before = await unchanged_facts(db_session)
    service = MixedDispositionService(MixedDispositionRepository(db_session))
    decision = request(row, decision_operation_id=payload["decision"], comment=payload["comment"])
    saved = await service.resolve(tenant_id=tenant_id, request=decision)
    result = saved.model_dump(mode="json")
    assert saved.resolved_at.utcoffset() is not None
    expected = prepare_mixed_disposition(
        request(row, decision_operation_id=payload["decision"], comment=payload["comment"]),
        row,
        tenant_id=tenant_id,
    ).model_dump(mode="json")
    expected["state"] = "resolved"
    content = {
        key: value for key, value in result.items() if key not in {"actor_id", "resolved_at"}
    }
    assert content == expected
    assert result["actor_id"] == str(runtime(db_session).actor_id)
    assert result["resolved_at"]
    assert_no_cost_fields(result)
    assert await unchanged_facts(db_session) == before
    remaining = await queue(db_session, tenant_id)
    assert len(remaining) == 1 and remaining[0].line_id != row.line_id
    history = (
        await db_session.execute(
            text("SELECT public.mixed_customer_return_disposition_history(:tenant)"),
            {"tenant": tenant_id},
        )
    ).scalars().all()
    assert history == [await record(db_session, payload)]
    assert (await service.resolve(tenant_id=tenant_id, request=decision)) == saved


async def test_real_resolve_only_reader_can_write_and_second_actor_replays_original_author(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    await db_session.commit()
    first_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.resolve",)
    )
    second_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.resolve",)
    )
    payload = parameters(row, context.stock.tenant_id)
    await set_receiving_context(db_session, context.stock, first_actor)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_return_command")) == 0
    assert await queue(db_session, context.stock.tenant_id) == []
    result = await record(db_session, payload)
    assert result["actor_id"] == str(first_actor)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, second_actor)
    assert await record(db_session, payload) == result
    await db_session.commit()
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("UPDATE user_assignment SET is_active=false WHERE user_id=:actor"),
            {"actor": second_actor},
        )
    await set_receiving_context(db_session, context.stock, second_actor)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, payload)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize(
    "scope", ["tenant", "branch", "view_only", "inactive", "missing_actor", "spoofed_support"]
)
async def test_real_database_scope_is_checked_before_saved_replay(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    payload = parameters(row, context.stock.tenant_id)
    original = await record(db_session, payload)
    await db_session.commit()
    other = context.stock
    if scope in {"tenant", "branch"}:
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                if scope == "tenant":
                    other = await seed_ledger_context(support)
                else:
                    branch = await support.scalar(
                        text(
                            "INSERT INTO branch(tenant_id,name) "
                            "VALUES(:tenant,'Decision branch') RETURNING id"
                        ),
                        {"tenant": context.stock.tenant_id},
                    )
                    assert isinstance(branch, UUID)
                    other = LedgerContext(context.stock.tenant_id, branch, context.stock.catalog_id)
    actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        other,
        permissions=(
            ("customer_returns.view",)
            if scope in {"view_only", "spoofed_support"}
            else ("customer_returns.resolve",)
        ),
    )
    if scope == "inactive":
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("UPDATE app_user SET status='blocked' WHERE id=:actor"), {"actor": actor}
            )
    await set_receiving_context(db_session, other, actor)
    if scope == "missing_actor":
        await db_session.execute(text("SELECT set_config('app.user_id','',true)"))
    elif scope == "spoofed_support":
        await db_session.execute(text("SELECT set_config('app.support_session','true',true)"))
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, payload)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    history = (
        await db_session.execute(
            text("SELECT public.mixed_customer_return_disposition_history(:tenant)"),
            {"tenant": context.stock.tenant_id},
        )
    ).scalars().all()
    assert history == ([original] if scope == "view_only" else [])


async def test_view_only_history_reads_saved_decision_without_raw_journal_access(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    result = await record(db_session, parameters(row, context.stock.tenant_id))
    await db_session.commit()
    viewer = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.view",)
    )
    await set_receiving_context(db_session, context.stock, viewer)
    history = (
        await db_session.execute(
            text("SELECT public.mixed_customer_return_disposition_history(:tenant)"),
            {"tenant": context.stock.tenant_id},
        )
    ).scalars().all()
    assert history == [result]
    absent = (
        await db_session.execute(
            text("SELECT public.mixed_customer_return_disposition_history(:tenant,:branch)"),
            {"tenant": context.stock.tenant_id, "branch": uuid4()},
        )
    ).scalars().all()
    assert absent == []
    foreign = (
        await db_session.execute(
            text("SELECT public.mixed_customer_return_disposition_history(:tenant)"),
            {"tenant": uuid4()},
        )
    ).scalars().all()
    assert foreign == []
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(text("SELECT * FROM mixed_customer_return_disposition"))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize("conflict", ["key_payload", "key_target", "new_key_same_target"])
async def test_key_and_full_target_uniqueness_conflicts(
    db_session: AsyncSession, conflict: str
) -> None:
    rows = await confirmed(db_session)
    tenant_id = runtime(db_session).stock.tenant_id
    payload = parameters(rows[0], tenant_id)
    original = await record(db_session, payload)
    if conflict == "key_payload":
        changed = {**payload, "comment": "Different decision content"}
    elif conflict == "key_target":
        changed = parameters(rows[1], tenant_id, operation_id=cast(UUID, payload["decision"]))
    else:
        changed = {**payload, "decision": uuid4()}
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, changed)
    assert getattr(error.value.orig, "sqlstate", None) == "23505"
    assert await record(db_session, payload) == original


async def test_distinct_lots_of_one_line_and_later_intake_are_independent_full_targets(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session, quantity=1)
    await retail_source(db_session, quantity=1)
    legacy = await legacy_with_stock(db_session, source.sale)
    physical = await put_physical(
        db_session, source.sale.id, position=2, sale_format="unit", quantity=32
    )
    original = await complete(db_session, await saved_request(db_session, source.sale))
    mixed = MixedSource(original, legacy.id, physical.id, legacy.batch_id)
    first = await refund(db_session, mixed, (PhysicalReturnSelection(physical.id, "unit", 31),))
    tenant_id = runtime(db_session).stock.tenant_id
    rows = [
        row for row in await queue(db_session, tenant_id) if row.operation_id == first.operation_id
    ]
    assert len(rows) == 2 and len({row.lot_id for row in rows}) == 2
    assert sorted(cast(int, row.base_units) for row in rows) == [1, 30]
    before = await unchanged_facts(db_session)
    first_result = await record(db_session, parameters(rows[0], tenant_id))
    remaining = await queue(db_session, tenant_id)
    assert len(remaining) == 1 and remaining[0].lot_id == rows[1].lot_id
    second_result = await record(db_session, parameters(rows[1], tenant_id))
    assert first_result["decision_operation_id"] != second_result["decision_operation_id"]
    assert await queue(db_session, tenant_id) == []
    assert await unchanged_facts(db_session) == before
    # A subsequent committed intake of the same original line/lot is another target.
    later = await refund(db_session, mixed, (PhysicalReturnSelection(physical.id, "unit", 1),))
    later_rows = await queue(db_session, tenant_id)
    assert len(later_rows) == 1 and later_rows[0].operation_id == later.operation_id
    assert later_rows[0].line_id == physical.id and later_rows[0].base_units == 1
    assert later_rows[0].lot_id in {row.lot_id for row in rows}
    await record(db_session, parameters(later_rows[0], tenant_id))
    assert await queue(db_session, tenant_id) == []


@pytest.mark.parametrize("status", ["readonly", "archived"])
async def test_current_tenant_policy_denies_replay_but_history_remains_readable(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, status: str
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    payload = parameters(row, context.stock.tenant_id)
    result = await record(db_session, payload)
    await db_session.commit()
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("UPDATE tenant SET status=:status WHERE id=:tenant"),
            {"status": status, "tenant": context.stock.tenant_id},
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        with pytest.raises(DBAPIError) as error:
            async with db_session.begin_nested():
                await record(db_session, payload)
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
        history = (
            await db_session.execute(
                text("SELECT public.mixed_customer_return_disposition_history(:tenant)"),
                {"tenant": context.stock.tenant_id},
            )
        ).scalars().all()
        assert history == [result]
    finally:
        await db_session.rollback()
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("UPDATE tenant SET status='active' WHERE id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )


@pytest.mark.parametrize("same_key", [True, False])
async def test_concurrent_requests_serialize_key_then_target(
    db_session: AsyncSession, same_key: bool
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    payload = parameters(row, context.stock.tenant_id)
    await db_session.commit()

    async def resolve(operation_id: UUID) -> dict[str, object] | str:
        try:
            async with context.factory.begin() as session:
                await set_receiving_context(session, context.stock, context.actor_id)
                return await record(session, {**payload, "decision": operation_id})
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "23505"
            return "conflict"

    first_id = cast(UUID, payload["decision"])
    results = await asyncio.wait_for(
        asyncio.gather(resolve(first_id), resolve(first_id if same_key else uuid4())), timeout=15
    )
    if same_key:
        assert isinstance(results[0], dict) and results[0] == results[1]
    else:
        assert sum(isinstance(result, dict) for result in results) == 1
        assert results.count("conflict") == 1


@pytest.mark.parametrize(
    "invalid", ["unmarked", "foreign_line", "foreign_stock", "both_stock", "restock"]
)
async def test_unconfirmed_or_forged_target_cannot_be_recorded(
    db_session: AsyncSession, invalid: str
) -> None:
    rows = await confirmed(db_session)
    row = next(row for row in rows if row.inventory_kind == "legacy_pack")
    payload = parameters(row, runtime(db_session).stock.tenant_id)
    state = "P0002"
    if invalid == "unmarked":
        source = await mixed_source(db_session)
        journal = await receive(db_session, source)
        payload["intake"] = journal.operation_id
        payload["line"] = source.legacy_id
        payload["batch"] = source.batch_id
    elif invalid == "foreign_line":
        payload["line"] = uuid4()
    elif invalid == "foreign_stock":
        payload["batch"] = uuid4()
    elif invalid == "both_stock":
        payload["lot"] = uuid4()
        state = "23514"
    else:
        payload["disposition"] = "restock"
        state = "23514"
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, payload)
    assert getattr(error.value.orig, "sqlstate", None) == state


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_owner_fingerprint_matches_fixed_python_utf8_golden_and_unicode_strip(
    maintenance_engine: AsyncEngine,
    kind: Literal["legacy_pack", "physical_unit"],
) -> None:
    row = contract_source(kind=kind)
    trim = "".join(chr(value) for value in range(0x3100) if chr(value).isspace())
    sql = text(
        """
      SELECT public.mixed_disposition_fingerprint(
        :tenant,:intake,:line,:kind,:batch,:lot,:disposition,:reason,:comment)
    """
    )
    async with maintenance_engine.connect() as connection:
        for comment in ("Претензия", trim + "Претензия" + trim, None, trim, 'Юникод "\\\nвнутри'):
            payload = parameters(row, UUID(int=1), comment=comment)
            actual = await connection.scalar(sql, payload)
            expected = prepare_mixed_disposition(
                request(row, comment=comment), row, tenant_id=UUID(int=1)
            ).request_hash
            assert actual == expected
            if kind == "legacy_pack" and comment is not None and comment.strip() == "Претензия":
                assert actual == "57f7d0bd0fd33cfb5ae3eb5a681ae2b93df6c2ee46fa43a46a8ddf29606dbcc9"


async def test_journal_acl_rls_private_helpers_and_safe_audit_immutability(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, db_engine: AsyncEngine
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    payload = parameters(row, context.stock.tenant_id, comment="Непубличный комментарий решения")
    result = await record(db_session, payload)
    await db_session.commit()
    module = migration()
    async with maintenance_engine.connect() as connection:
        relation = (
            await connection.execute(
                text(
                    """
          SELECT relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner
          FROM pg_class WHERE oid='public.mixed_customer_return_disposition'::regclass
        """
                )
            )
        ).mappings().one()
        assert relation["relrowsecurity"] and relation["relforcerowsecurity"]
        assert relation["owner"] == "aurum_schema_owner"
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            assert not await connection.scalar(
                text(
                    "SELECT has_table_privilege('aurum_app',"
                    "'public.mixed_customer_return_disposition',:p)"
                ),
                {"p": privilege},
            )
        for signature in (
            module.SOURCE,
            module.FINGERPRINT,
            module.PREPARE,
            "public.trg_mixed_disposition_guard()",
        ):
            assert not await connection.scalar(
                text("SELECT has_function_privilege('aurum_app',:s,'EXECUTE')"), {"s": signature}
            )
        snapshot = await connection.scalar(
            text(
                "SELECT new_values FROM audit_log "
                "WHERE table_name='mixed_customer_return_disposition' "
                "AND record_id=:id AND tenant_id=:tenant AND action='INSERT'"
            ),
            {"id": payload["decision"], "tenant": context.stock.tenant_id},
        )
        assert isinstance(snapshot, dict)
        assert snapshot["comment"] == snapshot["reason_code"] == snapshot["result_payload"] == "***"
        encoded = json.dumps(snapshot, ensure_ascii=False)
        assert cast(str, payload["comment"]) not in encoded and "damaged" not in encoded
        for statement in (
            "UPDATE mixed_customer_return_disposition SET comment='forged' WHERE id=:id",
            "DELETE FROM mixed_customer_return_disposition WHERE id=:id",
        ):
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.execute(text(statement), {"id": payload["decision"]})
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await record(db_session, payload) == result
    for statement in (
        "INSERT INTO mixed_customer_return_disposition(tenant_id,id) VALUES(:tenant,:id)",
        "SELECT * FROM mixed_customer_return_disposition",
    ):
        with pytest.raises(DBAPIError) as error:
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement), {"tenant": context.stock.tenant_id, "id": uuid4()}
                )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
    async with db_engine.connect() as support:
        with pytest.raises(DBAPIError) as error:
            await support.execute(WRITER, payload)
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


async def test_migration_roundtrip_or_history_refusal_keeps_prior_guards_and_queue(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        definition = text(
            """
          SELECT proname,pg_get_functiondef(oid),proacl,proowner FROM pg_proc
          WHERE pronamespace='public'::regnamespace AND proname IN (
            'mixed_customer_return_queue','audit_redact_table_snapshot','trg_mixed_return_guard',
            'trg_mixed_zero_return_guard','trg_mixed_cash_refund_guard') ORDER BY proname
        """
        )
        before = connection.execute(definition).all()
        exists = connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM mixed_customer_return_disposition)")
        )
        with Operations.context(MigrationContext.configure(connection)):
            if exists:
                with pytest.raises(DBAPIError) as error:
                    with connection.begin_nested():
                        migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
            else:
                migration().downgrade()
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('public.mixed_customer_return_disposition')")
                    )
                    is None
                )
                migration().upgrade()
        assert connection.execute(definition).all() == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
