"""Training journal schema, private guards and safe reversible empty-history upgrade."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.training_checkout_models import TrainingCheckout
from app.domains.pos.training_checkout_repository import TrainingCheckoutRepository
from app.domains.pos.training_checkout_schemas import TrainingCheckoutResultV1
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_training_checkout import complete, facts, training_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture

TABLE = "pos_training_checkout"
UNCHANGED = (
    "public.assert_physical_checkout(uuid,uuid,boolean)",
    "public.trg_physical_stock_checkout_binding()",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
    "public.trg_mixed_stock_checkout_binding()",
    "public.trg_require_sale_receipt_snapshot()",
    "public.trg_shift_unfinished_unit_cart()",
)


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0224_training_checkout.py"
    spec = importlib.util.spec_from_file_location("training_checkout_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_training_migration_hooks_are_private_bounded_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = migration()
    statements: list[str] = []
    patches: list[tuple[str, str, str, bool]] = []

    def capture(signature: str, old: str, new: str, *, upgrade: bool) -> None:
        patches.append((signature, old, new, upgrade))

    monkeypatch.setattr(module, "_sql", statements.append)
    monkeypatch.setattr(module, "_patch", capture)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.SQL.count("CREATE FUNCTION public.") == len(module.FUNCTIONS)
    assert module.SQL.count("END $$;") == len(module.FUNCTIONS)
    assert sql.count("CREATE CONSTRAINT TRIGGER training_checkout_complete") == 6
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "GRANT SELECT,INSERT ON public.pos_training_checkout TO aurum_app" in sql
    assert "GRANT SELECT ON public.pos_training_checkout TO aurum_support" in sql
    assert "training_checkout_refund_not_supported" in module.SQL
    assert "public.edge_canonical_jsonb(request)" in module.SQL
    assert "public.edge_canonical_jsonb(q)" in module.SQL
    expected = {
        "trg_guard_sale_immutability()",
        "trg_unit_cart_payment_boundary()",
        "audit_redact_table_snapshot(text,jsonb)",
    }
    assert {signature for signature, _, _, _ in patches} == expected
    assert all(signature.removeprefix("public.") not in expected for signature in UNCHANGED)
    applied = list(patches)
    statements.clear()
    module.downgrade()
    assert patches[len(applied) :] == [
        (signature, old, new, False) for signature, old, new, _ in reversed(applied)
    ]
    drops = "\n".join(statements)
    assert drops.index("downgrade_requires_empty_history") < drops.index("DROP TRIGGER")
    assert drops.index("DROP FUNCTION") < drops.index("DROP TABLE")
    assert "a02_training_checkout_return_boundary ON public.sale" in drops


def test_physical_if_case_does_not_end_plpgsql_condition_at_an_inner_then() -> None:
    sql = migration().SQL
    comparison = sql.index("receipt_line->>'unit_label' IS DISTINCT FROM")
    start = sql.rfind("\n   IF ", 0, comparison)
    end = sql.index("\n    RAISE EXCEPTION 'training_checkout_physical_source_invalid'", comparison)
    assert 0 <= start < comparison < end
    # PostgreSQL read_sql_construct stops at THEN outside ()/[], without
    # tracking CASE nesting. Exercise that boundary rather than exact SQL text.
    condition = sql[start:end]
    depth = 0
    terminators = 0
    for token in re.findall(r"'(?:''|[^'])*'|[()[\]]|\bTHEN\b", condition):
        if token in {"(", "["}:
            depth += 1
        elif token in {")", "]"}:
            depth -= 1
        elif token == "THEN" and depth == 0:
            terminators += 1
    assert depth == 0
    assert terminators == 1


async def test_training_schema_uses_least_privilege_and_private_function_ownership(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()
    async with maintenance_engine.connect() as connection:
        security = (await connection.execute(text("""
                    SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity,
                      has_table_privilege('aurum_app',oid,'SELECT'),
                      has_table_privilege('aurum_app',oid,'INSERT'),
                      has_table_privilege('aurum_app',oid,'UPDATE'),
                      has_table_privilege('aurum_app',oid,'DELETE'),
                      has_table_privilege('aurum_support',oid,'SELECT'),
                      has_table_privilege('aurum_support',oid,'INSERT'),
                      has_table_privilege('aurum_support',oid,'UPDATE'),
                      has_table_privilege('aurum_support',oid,'DELETE')
                    FROM pg_class WHERE oid='public.pos_training_checkout'::regclass
                """))).one()
        assert security == (
            "aurum_schema_owner",
            True,
            True,
            True,
            True,
            False,
            False,
            True,
            False,
            False,
            False,
        )
        for signature in module.FUNCTIONS:
            metadata = (
                await connection.execute(
                    text("""
                        SELECT pg_get_userbyid(proowner),prosecdef,proconfig,
                          has_function_privilege('aurum_app',oid,'EXECUTE'),
                          has_function_privilege('aurum_support',oid,'EXECUTE'),
                          EXISTS(SELECT 1 FROM aclexplode(proacl) WHERE grantee=0)
                        FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)
                    """),
                    {"signature": f"public.{signature}"},
                )
            ).one()
            assert metadata == (
                "aurum_schema_owner",
                True,
                ["search_path=pg_catalog, pg_temp"],
                False,
                False,
                False,
            )
        columns = dict((await connection.execute(text("""
                        SELECT attname,format_type(atttypid,atttypmod)
                        FROM pg_attribute WHERE attrelid='public.pos_training_checkout'::regclass
                          AND attname IN ('total_amount','created_at','receipt_seq')
                          AND NOT attisdropped
                    """))).tuples())
        assert columns == {
            "total_amount": "numeric(14,2)",
            "created_at": "timestamp with time zone",
            "receipt_seq": "bigint",
        }


async def test_training_migration_roundtrip_preserves_exact_real_guards_and_security(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        module = migration()
        definition = text("""
            SELECT pg_get_functiondef(oid),proowner,proacl::TEXT,prosecdef,proconfig
            FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)
        """)
        patched = tuple(f"public.{signature}" for signature, _, _ in module.PATCHES)
        before = {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in patched + UNCHANGED
        }
        security = text("""
            SELECT relowner,relacl::TEXT,relrowsecurity,relforcerowsecurity
            FROM pg_class WHERE oid='public.pos_training_checkout'::regclass
        """)
        table_before = connection.execute(security).one()
        functions_before = {
            signature: connection.execute(definition, {"signature": f"public.{signature}"}).one()
            for signature in module.FUNCTIONS
        }
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM public.pos_training_checkout)")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                    with connection.begin_nested():
                        module.downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
                return
            module.downgrade()
            assert connection.scalar(text("SELECT to_regclass(:table)"), {"table": TABLE}) is None
            assert {
                signature: connection.execute(definition, {"signature": signature}).one()
                for signature in UNCHANGED
            } == {signature: before[signature] for signature in UNCHANGED}
            module.upgrade()
        assert {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in patched + UNCHANGED
        } == before
        assert connection.execute(security).one() == table_before
        # Recreated functions receive new OIDs but must preserve definitions and ACLs.
        for signature, metadata in functions_before.items():
            assert (
                connection.execute(definition, {"signature": f"public.{signature}"}).one()
                == metadata
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_training_journal_rls_hides_other_tenants_and_cashiers(
    db_session: AsyncSession,
) -> None:
    _source, request = await training_source(db_session, mixed=True)
    await complete(db_session, request)
    context = runtime(db_session)
    count = text("SELECT count(*) FROM pos_training_checkout WHERE id=:id")
    assert await db_session.scalar(count, {"id": request.operation_id}) == 1
    try:
        await db_session.execute(
            text("SELECT set_config('app.tenant_id',:tenant,true)"),
            {"tenant": str(uuid4())},
        )
        assert await db_session.scalar(count, {"id": request.operation_id}) == 0
        await set_receiving_context(db_session, context.stock, uuid4())
        assert await db_session.scalar(count, {"id": request.operation_id}) == 0
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await db_session.scalar(count, {"id": request.operation_id}) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pos_training_checkout SET total_amount=total_amount+1 WHERE id=:id",
        "DELETE FROM pos_training_checkout WHERE id=:id",
        "UPDATE sale SET status='draft' WHERE id=:sale",
        "UPDATE pos_unit_cart_item SET quantity=quantity+1 WHERE sale_id=:sale",
        "DELETE FROM pos_unit_cart_item WHERE sale_id=:sale",
        "INSERT INTO sale_payment (tenant_id,sale_id,payment_method,amount,currency) "
        "SELECT tenant_id,id,'cash',total_amount,'TJS' FROM sale WHERE id=:sale",
        "INSERT INTO sale (tenant_id,branch_id,register_id,shift_id,cashier_user_id,"
        "parent_sale_id,sale_type,status,is_test,total_amount,currency) "
        "SELECT tenant_id,branch_id,register_id,shift_id,cashier_user_id,id,"
        "'return','draft',true,total_amount,'TJS' FROM sale WHERE id=:sale",
    ],
    ids=[
        "journal-update",
        "journal-delete",
        "sale-update",
        "cart-update",
        "cart-delete",
        "real-payment",
        "generic-training-refund",
    ],
)
async def test_finalized_training_documents_cannot_mutate_or_create_money(
    db_session: AsyncSession, statement: str
) -> None:
    _source, request = await training_source(db_session, mixed=True)
    await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement), {"id": request.operation_id, "sale": request.sale_id}
            )
    assert await facts(db_session) == before


async def test_training_journal_audit_excludes_both_document_payloads(
    db_session: AsyncSession,
) -> None:
    _source, request = await training_source(db_session, mixed=True)
    await complete(db_session, request)
    count, redacted = (
        await db_session.execute(
            text("""
                SELECT count(*),bool_and(old_values IS NULL AND
                  new_values-ARRAY['created_at','created_by']='{}'::JSONB)
                FROM audit_log WHERE table_name='pos_training_checkout'
                  AND record_id=:operation AND action='INSERT'
            """),
            {"operation": request.operation_id},
        )
    ).one()
    assert count == 1 and redacted is True


async def test_training_history_blocks_downgrade_without_touching_saved_result(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    _source, request = await training_source(db_session)
    await complete(db_session, request)
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                with connection.begin_nested():
                    migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        assert (
            connection.scalar(
                text("SELECT count(*) FROM pos_training_checkout WHERE id=:operation"),
                {"operation": request.operation_id},
            )
            == 1
        )

    async with maintenance_engine.begin() as connection:
        await connection.run_sync(refuse)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("missing-stock-date", "simulation_shape"),
        ("minimal-physical-before", "stock_shape"),
        ("minimal-physical-after", "stock_shape"),
        ("extra-result", "result_shape"),
        ("extra-receipt", "receipt_shape"),
        ("extra-request", "request_shape"),
        ("extra-quote", "quote_shape"),
        ("extra-simulation", "simulation_shape"),
        ("packaging-string-version", "packaging_shape"),
        ("packaging-float-version", "packaging_shape"),
        ("invalid-stock-created-at", "stock_shape"),
        ("missing-legacy-quantity", "legacy_stock_shape"),
        ("extra-opened-pack-field", "opened_pack_shape"),
        ("extra-quote-line", "quote_line_shape"),
        ("extra-receipt-line", "receipt_line_shape"),
    ],
)
async def test_raw_journal_rejects_unreadable_typed_payloads_before_persistence(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mutation: str, error: str
) -> None:
    _source, request = await training_source(db_session, mixed=True)
    before = await facts(db_session)
    insert_attempts = 0

    async def raw_insert(
        repository: TrainingCheckoutRepository,
        result: TrainingCheckoutResultV1,
        request_hash: str,
    ) -> None:
        nonlocal insert_attempts
        insert_attempts += 1
        payload: dict[str, object] = result.model_dump(mode="json")
        receipt: dict[str, object] = result.receipt.model_dump(mode="json")
        payload["receipt"] = receipt
        simulation = cast(dict[str, object], payload["simulation"])
        quote = cast(dict[str, object], simulation["quote"])
        physical_before = cast(list[dict[str, object]], simulation["physical_before"])
        physical_after = cast(list[dict[str, object]], simulation["physical_after"])
        if mutation == "missing-stock-date":
            simulation.pop("stock_date")
        elif mutation in {"minimal-physical-before", "minimal-physical-after"}:
            snapshots = physical_before if mutation.endswith("before") else physical_after
            snapshots[0] = {
                key: snapshots[0][key] for key in ("id", "tenant_id", "branch_id", "catalog_id")
            }
        elif mutation.startswith("extra-"):
            extra_targets: dict[str, dict[str, object]] = {
                "extra-result": payload,
                "extra-receipt": receipt,
                "extra-request": cast(dict[str, object], payload["request"]),
                "extra-quote": quote,
                "extra-simulation": simulation,
                "extra-opened-pack-field": cast(
                    list[dict[str, object]], physical_after[0]["opened_packs"]
                )[0],
                "extra-quote-line": cast(list[dict[str, object]], quote["lines"])[0],
                "extra-receipt-line": cast(list[dict[str, object]], receipt["lines"])[0],
            }
            extra_targets[mutation]["unexpected"] = True
        elif mutation in {"packaging-string-version", "packaging-float-version"}:
            packaging = cast(dict[str, object], physical_before[0]["packaging"])
            packaging["version"] = "2" if mutation.endswith("string-version") else 2.0
        elif mutation == "invalid-stock-created-at":
            physical_before[0]["created_at"] = "not-a-timestamp"
        elif mutation == "missing-legacy-quantity":
            cast(list[dict[str, object]], simulation["legacy_before"])[0].pop("quantity_remaining")
        else:
            raise AssertionError("unknown raw journal mutation")
        # Core INSERT deliberately bypasses all request/result Pydantic validation.
        # The database must reject this row before complete_sale can run.
        await repository.session.execute(
            insert(TrainingCheckout).values(
                id=result.operation_id,
                tenant_id=result.tenant_id,
                branch_id=result.branch_id,
                register_id=result.register_id,
                shift_id=result.shift_id,
                cashier_user_id=result.cashier_id,
                sale_id=result.sale_id,
                request_hash=request_hash,
                quote_revision=result.request.expected_revision_hash,
                total_amount=result.total_amount,
                receipt_number=result.receipt_number,
                receipt_seq=result.receipt_sequence,
                result_payload=payload,
                receipt_payload=receipt,
                created_by=result.cashier_id,
            )
        )

    monkeypatch.setattr(TrainingCheckoutRepository, "insert", raw_insert)
    with pytest.raises(DBAPIError, match=f"training_checkout_{error}_invalid"):
        await complete(db_session, request)
    assert insert_attempts == 1
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_training_checkout WHERE id=:operation"),
            {"operation": request.operation_id},
        )
        == 0
    )
