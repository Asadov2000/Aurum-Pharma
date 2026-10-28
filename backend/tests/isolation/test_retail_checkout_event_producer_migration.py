"""Gated producer schema, SQL parity and history protection in disposable CI."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.sql.elements import TextClause

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import SyncNodeCreate
from app.domains.sync.service import SyncAdminService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_retail_checkout_event_producer_integration import (
    enable_gate,
    event_facts,
    produce,
)
from tests.domains.pos.test_retail_checkout_origin_integration import finish, prepare
from tests.domains.pos.test_retail_checkout_source import result
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLE = "pos_retail_checkout_event"
GATE = "sync_retail_event_producer_gate"
UNCHANGED = (
    "public.assert_current_branch_writer(uuid,uuid,uuid,boolean)",
    "public.assert_physical_checkout(uuid,uuid,boolean)",
    "public.assert_mixed_checkout(uuid,uuid,boolean)",
    "public.trg_capture_retail_checkout_origin()",
)


def migration(filename: str = "0226_retail_checkout_events.py") -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions" / filename
    spec = importlib.util.spec_from_file_location("retail_event_producer_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_patch_preserves_historical_rehearsals_and_null_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = migration()
    physical = migration("0218_unit_electronic_refund_posting.py")
    mixed = migration("0223_mixed_electronic_refund_posting.py")
    training = migration("0224_training_checkout.py")
    signature = "audit_redact_table_snapshot(text,jsonb)"
    training_patch = next(patch for patch in training.PATCHES if patch[0] == signature)
    original = "BEGIN\n" + current.AUDIT_OLD + "\nRETURN v_result;\nEND;"

    class DefinitionConnection:
        definition = original

        def scalar(self, _statement: object, _parameters: object = None) -> str:
            return self.definition

        def exec_driver_sql(self, statement: str) -> None:
            self.definition = statement

        def execute(self, statement: TextClause) -> None:
            # 0224 escapes colon binds through Alembic op.execute(text(...)).
            # Compile that real SQL text representation; never evaluate it.
            assert isinstance(statement, TextClause)
            self.definition = str(statement)

    connection = DefinitionConnection()
    monkeypatch.setattr(current.op, "get_bind", lambda: connection)
    monkeypatch.setattr(training.op, "execute", connection.execute)
    physical._patch(signature, physical.AUDIT_OLD, physical.AUDIT_NEW, upgrade=True)
    mixed._patch(signature, mixed.AUDIT_OLD, mixed.AUDIT_NEW, upgrade=True)
    training._patch(*training_patch, upgrade=True)
    before_current = connection.definition
    current._audit(True)
    after_current = connection.definition
    assert after_current.count(training_patch[2]) == 1
    assert after_current.count(current.AUDIT_NEW) == 1
    # 0224 interrupts the older exact anchor even with the safe 0226 prefix.
    assert after_current.count(mixed.AUDIT_NEW) == 0
    assert after_current.count(current.AUDIT_OLD) == 1

    # The selective rehearsal peels only 0224's audit branch. Its table and
    # other training guards remain installed, as does the 0226 audit prefix.
    training._patch(*training_patch, upgrade=False)
    assert connection.definition.count(mixed.AUDIT_NEW) == 1
    assert connection.definition.count(current.AUDIT_NEW) == 1
    mixed._patch(signature, mixed.AUDIT_OLD, mixed.AUDIT_NEW, upgrade=False)
    assert connection.definition.count(physical.AUDIT_NEW) == 1
    physical._patch(signature, physical.AUDIT_OLD, physical.AUDIT_NEW, upgrade=False)
    assert connection.definition == original.replace(current.AUDIT_OLD, current.AUDIT_NEW, 1)
    physical._patch(signature, physical.AUDIT_OLD, physical.AUDIT_NEW, upgrade=True)
    mixed._patch(signature, mixed.AUDIT_OLD, mixed.AUDIT_NEW, upgrade=True)
    training._patch(*training_patch, upgrade=True)
    assert connection.definition == after_current
    current._audit(False)
    assert connection.definition == before_current

    # A NULL snapshot skips the new branch and reaches the unchanged NULL return.
    prefix, null_guard, suffix = current.AUDIT_NEW.partition(current.AUDIT_OLD)
    assert null_guard == current.AUDIT_OLD and suffix == ""
    condition, metadata_return = prefix.split(" THEN", 1)
    assert "AND v_result IS NOT NULL" in condition
    assert "RETURN jsonb_build_object" in metadata_return
    assert "'created_at',v_result->'created_at','created_by',v_result->'created_by'" in prefix
    assert "RETURN" not in condition


def test_migration_is_default_off_and_captures_its_decision_without_history_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = migration()
    statements: list[str] = []
    monkeypatch.setattr(module, "_sql", statements.append)
    monkeypatch.setattr(module, "_audit", lambda _upgrade: None)
    module.upgrade()
    sql = "\n".join(statements)
    assert module.revision == "0226" and module.down_revision == "0225"
    assert "enabled BOOLEAN NOT NULL DEFAULT false" in sql
    assert "event_capture_required BOOLEAN NOT NULL DEFAULT false" in sql
    assert "ADD COLUMN source_transaction_id BIGINT" in sql
    assert "FOR SHARE" in module.SQL and "txid_current()" in module.SQL
    assert "IS JSON OBJECT WITH UNIQUE KEYS" in module.SQL
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "trg_retail_checkout_event_required" in sql
    assert "record BYTEA NOT NULL" in sql
    assert "octet_length(record) BETWEEN 1 AND 1048576" in sql
    for statement in statements:
        if statement != module.SQL:
            assert "INSERT INTO public.pos_retail_checkout_event" not in statement
            assert "UPDATE public.pos_retail_checkout_origin" not in statement
    statements.clear()
    module.downgrade()
    downgrade = "\n".join(statements)
    assert downgrade.index("downgrade_requires_empty_history") < downgrade.index("DROP TRIGGER")
    assert downgrade.index("DROP FUNCTION") < downgrade.index("DROP TABLE")


async def test_gate_and_event_relations_have_exact_narrow_acl_and_private_helpers(
    maintenance_engine: AsyncEngine,
) -> None:
    module = migration()
    roles = (
        "aurum_app",
        "aurum_support",
        "aurum_worker",
        "aurum_catalog_worker",
        "aurum_billing_worker",
        "aurum_mailer",
        "aurum_edge_cash_owner",
        "aurum_edge_cash_executor",
    )
    async with maintenance_engine.connect() as connection:
        for table in (TABLE, GATE):
            assert (
                await connection.execute(
                    text(
                        "SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity "
                        "FROM pg_class WHERE oid=CAST(:table AS regclass)"
                    ),
                    {"table": f"public.{table}"},
                )
            ).one() == ("aurum_schema_owner", True, True)
            for role in roles:
                for privilege in (
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                ):
                    granted = await connection.scalar(
                        text("SELECT has_table_privilege(:role,:table,:privilege)"),
                        {"role": role, "table": f"public.{table}", "privilege": privilege},
                    )
                    assert granted is (
                        table == TABLE
                        and role in {"aurum_app", "aurum_support"}
                        and privilege == "SELECT"
                    )
        for signature in module.FUNCTIONS:
            for role in roles:
                granted = await connection.scalar(
                    text("SELECT has_function_privilege(:role,:signature,'EXECUTE')"),
                    {"role": role, "signature": f"public.{signature}"},
                )
                assert granted is (role == "aurum_app" and signature in module.PUBLIC_FUNCTIONS)
        default = await connection.scalar(text("""
                SELECT pg_get_expr(d.adbin,d.adrelid) FROM pg_attrdef d
                JOIN pg_attribute a ON a.attrelid=d.adrelid AND a.attnum=d.adnum
                WHERE d.adrelid='public.sync_retail_event_producer_gate'::regclass
                  AND a.attname='enabled'
            """))
        assert default == "false"


async def test_owner_cannot_enable_producer_with_an_existing_active_v1_edge_recipient(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await SyncAdminService(SyncCloudRepository(support)).create_node(
                SyncNodeCreate(
                    tenant_id=context.stock.tenant_id,
                    branch_id=context.stock.branch_id,
                    display_name="Existing v1 recipient",
                )
            )
    with pytest.raises(DBAPIError, match="retail_event_delivery_not_ready") as denied:
        await enable_gate(db_session, maintenance_engine)
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"


@pytest.mark.parametrize("kind", ["nested-unicode", "physical", "mixed"])
async def test_private_sql_canonicalization_and_hash_match_python_typed_records(
    maintenance_engine: AsyncEngine, kind: str
) -> None:
    value = (
        {
            "я": [
                None,
                True,
                False,
                0,
                2**63 - 1,
                {"a": 'строка\n\t"\\', "💊": "таблетка"},
            ],
            "a": {"z": "decimal amounts remain strings", "cost": "0.01"},
        }
        if kind == "nested-unicode"
        else result("physical" if kind == "physical" else "mixed").model_dump(mode="json")
    )
    async with maintenance_engine.connect() as connection:
        canonical, digest = (
            await connection.execute(
                text(
                    "SELECT public.retail_event_canonical_json(CAST(:value AS JSONB)),"
                    "public.retail_event_hash(CAST(:value AS JSONB))"
                ),
                {"value": json.dumps(value, ensure_ascii=False)},
            )
        ).one()
    assert canonical.encode("utf-8") == canonical_json_bytes(value)
    assert digest == canonical_json_hash(value)


async def test_runtime_cannot_mutate_event_history_gate_or_execute_private_helpers(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    await enable_gate(db_session, maintenance_engine)
    source = await finish(db_session, await prepare(db_session, "physical-cash"))
    before = await event_facts(db_session)
    statements = (
        "INSERT INTO pos_retail_checkout_event SELECT * FROM pos_retail_checkout_event",
        "UPDATE pos_retail_checkout_event SET sequence=sequence+1 WHERE id=:id",
        "DELETE FROM pos_retail_checkout_event WHERE id=:id",
        "TRUNCATE pos_retail_checkout_event",
        "SELECT * FROM sync_retail_event_producer_gate",
        "UPDATE sync_retail_event_producer_gate SET enabled=false",
        "SELECT public.retail_event_canonical_json('{}'::JSONB)",
        "SELECT public.retail_event_hash('{}'::JSONB)",
        "DO $$ BEGIN PERFORM public.trg_retail_event_capture(); END $$",
        "DO $$ BEGIN PERFORM public.trg_retail_checkout_event_required(); END $$",
    )
    for statement in statements:
        with pytest.raises(DBAPIError) as denied:
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": source.operation_id})
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await event_facts(db_session) == before
    audit = (
        await db_session.execute(
            text(
                "SELECT action,new_values FROM audit_log WHERE tenant_id=:tenant "
                "AND table_name='pos_retail_checkout_event' AND record_id=:id"
            ),
            {"tenant": source.tenant_id, "id": source.operation_id},
        )
    ).one()
    assert audit.action == "INSERT"
    assert set(audit.new_values) == {"created_at", "created_by"}
    assert audit.new_values["created_by"] == str(source.cashier_id)


async def test_empty_roundtrip_or_required_history_refusal_preserves_canonical_existing_guards(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        module = migration()
        definition = text(
            "SELECT pg_get_functiondef(oid),proacl::TEXT,proconfig "
            "FROM pg_proc WHERE oid=CAST(:signature AS regprocedure)"
        )
        before = {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in UNCHANGED
        }
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_retail_checkout_event)")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                    with connection.begin_nested():
                        module.downgrade()
                assert getattr(denied.value.orig, "sqlstate", None) == "55000"
            else:
                module.downgrade()
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('public.pos_retail_checkout_event')")
                    )
                    is None
                )
                module.upgrade()
        assert {
            signature: connection.execute(definition, {"signature": signature}).one()
            for signature in UNCHANGED
        } == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_committed_event_history_blocks_owner_update_delete_truncate_and_downgrade(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    await enable_gate(db_session, maintenance_engine)
    source = await finish(db_session, await prepare(db_session, "mixed-cash"))
    saved = await produce(db_session, source)
    assert saved is not None
    context = runtime(db_session)
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    await db_session.commit()

    def refuse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        for statement in (
            "UPDATE pos_retail_checkout_event SET sequence=sequence+1 WHERE id=:id",
            "DELETE FROM pos_retail_checkout_event WHERE id=:id",
            "TRUNCATE pos_retail_checkout_event",
        ):
            with pytest.raises(DBAPIError, match="retail_event_immutable") as denied:
                with connection.begin_nested():
                    connection.execute(text(statement), {"id": source.operation_id})
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history") as denied:
                with connection.begin_nested():
                    migration().downgrade()
            assert getattr(denied.value.orig, "sqlstate", None) == "55000"

    async with maintenance_engine.begin() as connection:
        await connection.run_sync(refuse)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await produce(db_session, source) == saved
