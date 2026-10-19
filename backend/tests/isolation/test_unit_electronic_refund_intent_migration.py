"""Review-intent schema rehearsal on a disposable database, never financial writes."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.isolation.test_physical_cash_checkout_migration import (
    electronic_refund_activation_migration,
    electronic_refund_binding_migration,
)

db_session = receiving_session_fixture

TABLE = "pos_unit_electronic_refund_intent"


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / (
        "alembic/versions/0214_unit_electronic_refund_intents.py"
    )
    spec = importlib.util.spec_from_file_location("electronic_refund_intents", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_refund_intent_roundtrip_preserves_security(maintenance_engine: AsyncEngine) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        audit_sql = text(
            "SELECT pg_get_functiondef("
            "'public.audit_redact_table_snapshot(text,jsonb)'::regprocedure)"
        )
        before_audit = connection.scalar(audit_sql)
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text(f"SELECT EXISTS(SELECT 1 FROM {TABLE})")):
                with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                    with connection.begin_nested():
                        migration().downgrade()
                return
            electronic_refund_activation_migration().downgrade()
            electronic_refund_binding_migration().downgrade()
            migration().downgrade()
            assert connection.scalar(text(f"SELECT to_regclass('public.{TABLE}')")) is None
            migration().upgrade()
            electronic_refund_binding_migration().upgrade()
            electronic_refund_activation_migration().upgrade()
        assert connection.scalar(audit_sql) == before_audit
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity AND "
                "relowner='aurum_schema_owner'::regrole FROM pg_class "
                f"WHERE oid='public.{TABLE}'::regclass"
            )
        )
        for role, privilege, expected in (
            ("aurum_app", "SELECT", True),
            ("aurum_app", "INSERT", True),
            ("aurum_app", "UPDATE", False),
            ("aurum_app", "DELETE", False),
            ("aurum_support", "SELECT", True),
            ("aurum_support", "INSERT", False),
            ("aurum_support", "UPDATE", False),
            ("aurum_support", "DELETE", False),
        ):
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege(:role,:table,:privilege)"),
                    {"role": role, "table": f"public.{TABLE}", "privilege": privilege},
                )
                is expected
            )
        policies = connection.execute(
            text(
                "SELECT policyname,qual,with_check FROM pg_policies "
                "WHERE schemaname='public' AND tablename=:table ORDER BY policyname"
            ),
            {"table": TABLE},
        ).all()
        assert [row.policyname for row in policies] == ["schema_owner", "support", "tenant_actor"]
        actor_policy = policies[2]
        assert "actor_id = current_app_user_id()" in actor_policy.qual
        assert "pos.refund" in actor_policy.qual
        assert "pos.refund" in actor_policy.with_check
        assert connection.scalar(
            text("SELECT public.audit_redact_table_snapshot(:table,CAST(:snapshot AS jsonb))"),
            {
                "table": TABLE,
                "snapshot": (
                    '{"request_payload":{"private":true},"result_payload":{},'
                    '"request_hash":"retained"}'
                ),
            },
        ) == {"request_hash": "retained"}
        guard = connection.scalar(
            text(
                "SELECT pg_get_functiondef('public.trg_unit_electronic_refund_intent_guard()'"
                "::regprocedure)"
            )
        )
        assert "SECURITY DEFINER" in guard
        assert "SET search_path TO 'pg_catalog', 'pg_temp'" in guard
        assert (
            connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_app',"
                    "'public.trg_unit_electronic_refund_intent_guard()','EXECUTE')"
                )
            )
            is False
        )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_guard_rejects_forged_payment_origin(db_session: AsyncSession) -> None:
    from uuid import uuid4

    from app.domains.pos.unit_electronic_refund_intent_repository import (
        UnitElectronicRefundIntentRepository,
    )
    from app.domains.pos.unit_electronic_refund_intent_service import (
        UnitElectronicRefundIntentService,
    )
    from tests.domains.pos.test_unit_electronic_refund_intent import save_review
    from tests.domains.pos.test_unit_return import runtime

    request = await save_review(db_session)
    context = runtime(db_session)
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    with pytest.raises(DBAPIError, match="unit_electronic_refund_intent_payment_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO {TABLE} (tenant_id,id,parent_sale_id,branch_id,actor_id,"
                    "request_hash,request_payload,result_payload,created_by) "
                    "SELECT tenant_id,:new_id,parent_sale_id,branch_id,actor_id,request_hash,"
                    "request_payload,jsonb_set(result_payload,'{payments,0,original_payment_id}',"
                    "to_jsonb(CAST(:forged_id AS text))),created_by "
                    f"FROM {TABLE} WHERE tenant_id=:tenant AND id=:id"
                ),
                {
                    "new_id": uuid4(),
                    "forged_id": str(uuid4()),
                    "tenant": context.stock.tenant_id,
                    "id": request.operation_id,
                },
            )


async def test_nonempty_downgrade_rejected(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    from app.domains.pos.unit_electronic_refund_intent_repository import (
        UnitElectronicRefundIntentRepository,
    )
    from app.domains.pos.unit_electronic_refund_intent_service import (
        UnitElectronicRefundIntentService,
    )
    from tests.domains.pos.test_unit_electronic_refund_intent import save_review
    from tests.domains.pos.test_unit_return import runtime

    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    request = await save_review(db_session)
    context = runtime(db_session)
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    await db_session.commit()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        query = text(f"SELECT to_jsonb(row) FROM {TABLE} row WHERE tenant_id=:tenant AND id=:id")
        parameters = {"tenant": context.stock.tenant_id, "id": request.operation_id}
        before = connection.scalar(query, parameters)
        assert before is not None
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(DBAPIError, match="downgrade_requires_empty_history"):
                with connection.begin_nested():
                    migration().downgrade()
        assert connection.scalar(query, parameters) == before

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
