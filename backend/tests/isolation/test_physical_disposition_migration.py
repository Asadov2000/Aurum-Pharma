"""Physical quarantine decisions enforce real scopes without changing stock or money."""

import asyncio
import hashlib
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
from app.domains.customer_returns.physical_repository import PhysicalCustomerReturnsRepository
from app.domains.customer_returns.physical_schemas import PhysicalCustomerReturnRead
from app.domains.customer_returns.physical_service import PhysicalCustomerReturnsService
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService
from tests.domains.customer_returns.test_physical_queue import cash_source
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import facts
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_physical_return_journal import receive
from tests.domains.pos.test_physical_zero_return import source as zero_source
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
WRITER = text("""
 SELECT public.record_physical_customer_return_disposition(
   :tenant,:decision,:intake,:line,:lot,:disposition,:reason,:comment)
""")


def migration() -> ModuleType:
    directory = Path(__file__).resolve().parents[2] / "alembic/versions"
    path = directory / "0198_physical_quarantine_disposition.py"
    spec = importlib.util.spec_from_file_location("physical_disposition_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def queue(session: AsyncSession, tenant_id: UUID) -> list[PhysicalCustomerReturnRead]:
    result = await PhysicalCustomerReturnsService(
        PhysicalCustomerReturnsRepository(session)
    ).list_returns(
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
) -> list[PhysicalCustomerReturnRead]:
    if intake_kind == "cash_refund":
        _, cash = await cash_source(session)
        operation_id = cash.operation_id
    else:
        original = await zero_source(session)
        selections = (PhysicalReturnSelection(original.lines[0].id, "unit", 1),)
        zero = await PhysicalZeroReturnService(PhysicalZeroReturnRepository(session)).receive(
            tenant_id=original.tenant_id,
            actor_id=original.cashier_id,
            parent_sale_id=original.sale_id,
            operation_id=uuid4(),
            selections=selections,
            expected_revision_hash=physical_refund_revision(original, {}, selections),
        )
        operation_id = zero.operation_id
    rows = await queue(session, runtime(session).stock.tenant_id)
    return [row for row in rows if row.operation_id == operation_id]


def parameters(
    row: PhysicalCustomerReturnRead,
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
        "lot": row.lot_id,
        "disposition": "disposed",
        "reason": "damaged",
        "comment": comment,
    }


async def record(session: AsyncSession, payload: dict[str, object]) -> dict[str, object]:
    result = await session.scalar(WRITER, payload)
    assert isinstance(result, dict)
    return cast(dict[str, object], result)


@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
async def test_confirmed_whole_target_replay_history_status_and_no_stock_money_writes(
    db_session: AsyncSession,
    intake_kind: Literal["cash_refund", "zero_return"],
) -> None:
    row = (await confirmed(db_session, intake_kind=intake_kind))[0]
    tenant_id = runtime(db_session).stock.tenant_id
    payload = parameters(row, tenant_id, comment="  Проверка решения  ")
    before = {k: v for k, v in (await facts(db_session)).items() if k != "audit_log"}
    saved = await record(db_session, payload)
    assert saved["document_kind"] == "physical_customer_return_disposition"
    assert saved["state"] == "resolved" and saved["intake_kind"] == intake_kind
    assert saved["comment"] == "Проверка решения"
    assert saved["target"] == {
        "inventory_kind": "physical_unit",
        "intake_operation_id": str(row.operation_id),
        "line_id": str(row.line_id),
        "lot_id": str(row.lot_id),
        "base_units": row.base_units,
        "unit_label": row.unit_label,
    }
    assert_no_cost_fields(saved)
    assert await record(db_session, payload) == saved
    assert await queue(db_session, tenant_id) == []
    assert {k: v for k, v in (await facts(db_session)).items() if k != "audit_log"} == before
    status_sql = text("SELECT public.physical_customer_return_disposition_status(:t,:b,:d)")
    assert (
        await db_session.scalar(
            status_sql, {"t": tenant_id, "b": row.branch_id, "d": payload["decision"]}
        )
        == saved
    )
    missing = uuid4()
    assert await db_session.scalar(
        status_sql, {"t": tenant_id, "b": row.branch_id, "d": missing}
    ) == {
        "state": "not_found",
        "branch_id": str(row.branch_id),
        "decision_operation_id": str(missing),
    }


async def test_fingerprint_uses_canonical_domain_and_unicode_comment(
    maintenance_engine: AsyncEngine,
) -> None:
    target = {
        "inventory_kind": "physical_unit",
        "intake_operation_id": str(UUID(int=2)),
        "line_id": str(UUID(int=3)),
        "lot_id": str(UUID(int=4)),
    }
    trim = "".join(chr(value) for value in range(0x3100) if chr(value).isspace())
    async with maintenance_engine.connect() as connection:
        for comment in (None, trim, trim + "Претензия" + trim, 'Юникод "\\\nвнутри'):
            clean = comment.strip() if comment else ""
            canonical = {
                "version": 1,
                "domain": "physical_customer_return_disposition",
                "tenant_id": str(UUID(int=1)),
                "target": target,
                "disposition_type": "disposed",
                "reason_code": "damaged",
                "comment_hash": hashlib.sha256(clean.encode()).hexdigest() if clean else None,
            }
            expected = hashlib.sha256(
                json.dumps(
                    canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
            actual = await connection.scalar(
                text("""
                  SELECT public.physical_disposition_fingerprint(
                    :t,:i,:l,:lot,'disposed','damaged',:c)
                """),
                {
                    "t": UUID(int=1),
                    "i": UUID(int=2),
                    "l": UUID(int=3),
                    "lot": UUID(int=4),
                    "c": comment,
                },
            )
            assert actual == expected


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
    assert await db_session.scalar(text("SELECT count(*) FROM pos_physical_return_command")) == 0
    assert await queue(db_session, context.stock.tenant_id) == []
    result = await record(db_session, payload)
    assert result["actor_id"] == str(first_actor)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, second_actor)
    assert await record(db_session, payload) == result
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": second_actor, "tenant": context.stock.tenant_id},
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
        assert await db_session.scalar(
            text(
                "SELECT session_user='aurum_app' AND NOT public.is_support_session() "
                "AND NOT public.is_tenant_support_session()"
            )
        )
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, payload)
    assert getattr(error.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError) as status_error:
        async with db_session.begin_nested():
            await db_session.scalar(
                text("SELECT public.physical_customer_return_disposition_status(:t,:b,:d)"),
                {"t": context.stock.tenant_id, "b": row.branch_id, "d": payload["decision"]},
            )
    assert getattr(status_error.value.orig, "sqlstate", None) == "42501"
    history = (
        (
            await db_session.execute(
                text("SELECT public.physical_customer_return_disposition_history(:tenant)"),
                {"tenant": context.stock.tenant_id},
            )
        )
        .scalars()
        .all()
    )
    # A forged support flag changes neither legitimate view access nor resolve authority.
    assert history == ([original] if scope in {"view_only", "spoofed_support"} else [])


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
        (
            await db_session.execute(
                text("SELECT public.physical_customer_return_disposition_history(:tenant)"),
                {"tenant": context.stock.tenant_id},
            )
        )
        .scalars()
        .all()
    )
    assert history == [result]
    absent = (
        (
            await db_session.execute(
                text("SELECT public.physical_customer_return_disposition_history(:tenant,:branch)"),
                {"tenant": context.stock.tenant_id, "branch": uuid4()},
            )
        )
        .scalars()
        .all()
    )
    assert absent == []
    foreign = (
        (
            await db_session.execute(
                text("SELECT public.physical_customer_return_disposition_history(:tenant)"),
                {"tenant": uuid4()},
            )
        )
        .scalars()
        .all()
    )
    assert foreign == []
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await db_session.execute(text("SELECT * FROM physical_customer_return_disposition"))
    assert getattr(error.value.orig, "sqlstate", None) == "42501"


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
            (
                await db_session.execute(
                    text("SELECT public.physical_customer_return_disposition_history(:tenant)"),
                    {"tenant": context.stock.tenant_id},
                )
            )
            .scalars()
            .all()
        )
        assert history == [result]
    finally:
        await db_session.rollback()


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
        relation = (await connection.execute(text("""
          SELECT relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner
          FROM pg_class WHERE oid='public.physical_customer_return_disposition'::regclass
        """))).mappings().one()
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
                    "'public.physical_customer_return_disposition',:p)"
                ),
                {"p": privilege},
            )
        for signature in (
            module.SOURCE,
            module.FINGERPRINT,
            module.PREPARE,
            "public.trg_physical_disposition_guard()",
        ):
            assert not await connection.scalar(
                text("SELECT has_function_privilege('aurum_app',:s,'EXECUTE')"), {"s": signature}
            )
        snapshot = await connection.scalar(
            text(
                "SELECT new_values FROM audit_log "
                "WHERE table_name='physical_customer_return_disposition' "
                "AND record_id=:id AND tenant_id=:tenant AND action='INSERT'"
            ),
            {"id": payload["decision"], "tenant": context.stock.tenant_id},
        )
        assert isinstance(snapshot, dict)
        assert snapshot["comment"] == snapshot["reason_code"] == snapshot["result_payload"] == "***"
        encoded = json.dumps(snapshot, ensure_ascii=False)
        assert cast(str, payload["comment"]) not in encoded and "damaged" not in encoded
        for statement in (
            "UPDATE physical_customer_return_disposition SET comment='forged' WHERE id=:id",
            "DELETE FROM physical_customer_return_disposition WHERE id=:id",
        ):
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.execute(text(statement), {"id": payload["decision"]})
            assert getattr(error.value.orig, "sqlstate", None) == "55000"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await record(db_session, payload) == result
    for statement in (
        "INSERT INTO physical_customer_return_disposition(tenant_id,id) VALUES(:tenant,:id)",
        "SELECT * FROM physical_customer_return_disposition",
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
        definition = text("""
          SELECT proname,pg_get_functiondef(oid),proacl,proowner FROM pg_proc
          WHERE pronamespace='public'::regnamespace AND proname IN (
            'physical_customer_return_queue','audit_redact_table_snapshot',
            'trg_physical_return_guard',
            'trg_physical_zero_return_guard','trg_physical_cash_refund_guard') ORDER BY proname
        """)
        before = connection.execute(definition).all()
        exists = connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM physical_customer_return_disposition)")
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
                        text("SELECT to_regclass('public.physical_customer_return_disposition')")
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


@pytest.mark.parametrize("changed", ["reason", "decision", "line", "lot"])
async def test_operation_and_target_identity_conflicts(
    db_session: AsyncSession, changed: str
) -> None:
    row = (await confirmed(db_session))[0]
    payload = parameters(row, runtime(db_session).stock.tenant_id)
    await record(db_session, payload)
    replacement: object = "other" if changed == "reason" else uuid4()
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, {**payload, changed: replacement})
    assert getattr(error.value.orig, "sqlstate", None) == (
        "P0002" if changed in {"line", "lot"} else "23505"
    )


async def test_prepared_quarantine_without_confirmed_marker_cannot_resolve(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id)
    original = await complete(db_session, await saved_request(db_session, source.sale))
    intake = await receive(db_session, original.sale_id, item.id)
    payload = {
        "tenant": original.tenant_id,
        "decision": uuid4(),
        "intake": intake.operation_id,
        "line": item.id,
        "lot": intake.lines[0].slices[0].lot_id,
        "disposition": "disposed",
        "reason": "damaged",
        "comment": None,
    }
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await record(db_session, payload)
    assert getattr(error.value.orig, "sqlstate", None) == "P0002"
    assert await queue(db_session, original.tenant_id) == []


async def test_status_is_stable_fixed_owner_app_only_and_support_cannot_execute(
    maintenance_engine: AsyncEngine, db_engine: AsyncEngine
) -> None:
    async with maintenance_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""
                      SELECT pg_get_userbyid(proowner) AS owner,prosecdef,
                        provolatile::TEXT,proconfig,
                        has_function_privilege('aurum_app',oid,'EXECUTE') AS app_execute,
                        has_function_privilege('aurum_app',oid,
                          'EXECUTE WITH GRANT OPTION') AS grantable,
                        has_function_privilege('aurum_support',oid,'EXECUTE') AS support_execute,
                        EXISTS(SELECT 1 FROM aclexplode(proacl) a
                          WHERE a.grantee=0) AS public_access
                      FROM pg_proc WHERE oid=to_regprocedure(:signature)
                    """),
                    {"signature": migration().STATUS},
                )
            )
            .mappings()
            .one()
        )
        assert row["owner"] == "aurum_schema_owner" and row["prosecdef"] is True
        assert row["provolatile"] == "s" and row["proconfig"] == ["search_path=pg_catalog, pg_temp"]
        assert row["app_execute"] is True
        assert not row["grantable"] and not row["support_execute"] and not row["public_access"]
    async with db_engine.connect() as support:
        assert await support.scalar(text("SELECT session_user")) == "aurum_support"
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        with pytest.raises(DBAPIError) as error:
            await support.execute(
                text("SELECT public.physical_customer_return_disposition_status(:t,:b,:d)"),
                {"t": uuid4(), "b": uuid4(), "d": uuid4()},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
