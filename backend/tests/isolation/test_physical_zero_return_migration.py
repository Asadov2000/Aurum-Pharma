"""Zero-return storage independently rejects forged and cross-tenant history."""

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_zero_return_models import PhysicalZeroReturn
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_return_journal import receive
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request

db_session = receiving_session_fixture

FUNCTIONS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
    "WHERE pronamespace='public'::regnamespace AND proname IN "
    "('trg_physical_zero_return_guard','audit_redact_table_snapshot',"
    "'trg_physical_return_guard','trg_physical_return_complete',"
    "'trg_physical_cash_refund_guard','trg_guard_sale_immutability') ORDER BY proname"
)
RELATION = text(
    "SELECT relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
    "WHERE oid='public.pos_physical_zero_return'::regclass"
)
POLICIES = text(
    "SELECT policyname,roles,cmd,qual,with_check FROM pg_policies "
    "WHERE schemaname='public' AND tablename='pos_physical_zero_return' ORDER BY policyname"
)
HISTORY = text("SELECT to_jsonb(row) FROM pos_physical_zero_return row ORDER BY tenant_id,id")


def migration() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0186_physical_zero_return.py"
    spec = importlib.util.spec_from_file_location("physical_zero_return_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fingerprint(marker: PhysicalZeroReturn, journal_hash: str) -> str:
    payload = {
        "actor_id": str(marker.actor_id),
        "comment_hash": (
            hashlib.sha256(marker.comment.encode()).hexdigest()
            if marker.comment is not None
            else None
        ),
        "journal_request_hash": journal_hash,
        "kind": "physical_zero_return",
        "parent_sale_id": str(marker.parent_sale_id),
        "reason_code": marker.reason_code,
        "review_hash": marker.review_hash,
        "tenant_id": str(marker.tenant_id),
        "version": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


async def prepared_marker(
    session: AsyncSession, *, positive: bool = False
) -> tuple[PhysicalZeroReturn, str]:
    source = await checkout_source(session)
    item = await put_physical(
        session,
        source.sale.id,
        sale_format="pack",
        discount=Decimal("0.00") if positive else Decimal("59.99"),
    )
    await complete(session, await saved_request(session, source.sale))
    journal = await receive(session, source.sale.id, item.id)
    assert (journal.total_amount > 0) if positive else journal.total_amount == 0
    command = await PhysicalReturnJournalRepository(session).get_command(
        journal.tenant_id, journal.operation_id
    )
    assert command is not None
    marker = PhysicalZeroReturn(
        tenant_id=journal.tenant_id,
        id=journal.operation_id,
        parent_sale_id=journal.parent_sale_id,
        branch_id=journal.branch_id,
        actor_id=journal.actor_id,
        review_hash="a" * 64,
        reason_code="other",
        comment="Причина проверки истории",
        result_payload={
            "version": 1,
            "kind": "physical_zero_return",
            "operation_id": str(journal.operation_id),
            "parent_sale_id": str(journal.parent_sale_id),
            "tenant_id": str(journal.tenant_id),
            "branch_id": str(journal.branch_id),
            "actor_id": str(journal.actor_id),
            "state": "prepared_quarantine",
            "currency": "TJS",
            "total_amount": "0.00",
            "lines": [
                {"line_id": str(line.line_id), "base_units": line.base_units, "offset": line.offset}
                for line in journal.lines
            ],
        },
        created_by=journal.actor_id,
        updated_by=journal.actor_id,
    )
    marker.request_hash = fingerprint(marker, command.request_hash)
    return marker, command.request_hash


@pytest.mark.parametrize(
    "corruption",
    ["hash", "payload", "cost", "line", "reason", "comment", "review", "parent", "actor"],
)
async def test_zero_return_guard_rejects_forged_marker(
    db_session: AsyncSession, corruption: str
) -> None:
    marker, journal_hash = await prepared_marker(db_session)
    if corruption == "payload":
        marker.result_payload["total_amount"] = "1.00"
    elif corruption == "cost":
        marker.result_payload["total_cost_amount"] = "0.00"
    elif corruption == "line":
        marker.result_payload["lines"] = [{"line_id": str(uuid4()), "base_units": 1, "offset": 0}]
    elif corruption == "reason":
        marker.reason_code = "invalid"
    elif corruption == "comment":
        marker.comment = "\u2003Untrimmed comment\u2003"
    elif corruption == "review":
        marker.review_hash = "A" * 64
    elif corruption == "parent":
        marker.parent_sale_id = uuid4()
    elif corruption == "actor":
        marker.actor_id = uuid4()
    marker.request_hash = "0" * 64 if corruption == "hash" else fingerprint(marker, journal_hash)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await PhysicalZeroReturnRepository(db_session).insert(marker)
    assert getattr(error.value.orig, "sqlstate", None) in {"23514", "42501"}
    assert await PhysicalZeroReturnRepository(db_session).get(marker.tenant_id, marker.id) is None


async def test_zero_marker_rejects_positive_journal(db_session: AsyncSession) -> None:
    marker, _ = await prepared_marker(db_session, positive=True)
    with pytest.raises(DBAPIError) as error:
        async with db_session.begin_nested():
            await PhysicalZeroReturnRepository(db_session).insert(marker)
    assert "physical_zero_return_scope_invalid" in str(error.value.orig)


async def test_zero_marker_is_immutable_and_audit_redacts_document(
    db_session: AsyncSession,
) -> None:
    marker, _ = await prepared_marker(db_session)
    repository = PhysicalZeroReturnRepository(db_session)
    await repository.insert(marker)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await repository.get(marker.tenant_id, marker.id) is marker
    for statement in (
        "UPDATE pos_physical_zero_return SET branch_id=branch_id WHERE id=:id",
        "DELETE FROM pos_physical_zero_return WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": marker.id})
    audit = (
        await db_session.execute(
            text(
                "SELECT new_values FROM audit_log WHERE tenant_id=:tenant "
                "AND table_name='pos_physical_zero_return' AND record_id=:id AND action='INSERT'"
            ),
            {"tenant": marker.tenant_id, "id": marker.id},
        )
    ).scalar_one()
    assert audit["result_payload"] == "***"
    assert audit["comment"] == "***"
    assert marker.created_by == marker.actor_id and marker.updated_by == marker.actor_id
    assert marker.created_at == marker.updated_at


async def test_zero_marker_cannot_be_read_or_inserted_from_other_tenant(
    db_session: AsyncSession,
) -> None:
    marker, _ = await prepared_marker(db_session)
    repository = PhysicalZeroReturnRepository(db_session)
    await repository.insert(marker)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await repository.get(marker.tenant_id, marker.id) is None
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_physical_zero_return "
                    "(tenant_id,id,parent_sale_id,branch_id,actor_id,request_hash,review_hash,"
                    "result_payload,created_by,updated_by) "
                    "VALUES(:tenant,:id,:sale,:branch,:actor,:hash,:hash,'{}'::jsonb,:actor,:actor)"
                ),
                {
                    "tenant": marker.tenant_id,
                    "id": uuid4(),
                    "sale": marker.parent_sale_id,
                    "branch": marker.branch_id,
                    "actor": marker.actor_id,
                    "hash": "a" * 64,
                },
            )


def _refuse_without_changes(connection: Connection) -> None:
    before = connection.execute(FUNCTIONS).all()
    history = connection.execute(HISTORY).scalars().all()
    with pytest.raises(DBAPIError) as error:
        with connection.begin_nested():
            migration().downgrade()
    assert getattr(error.value.orig, "sqlstate", None) == "55000"
    assert "physical_zero_return_history_exists" in str(error.value.orig)
    assert connection.execute(FUNCTIONS).all() == before
    assert connection.execute(HISTORY).scalars().all() == history


async def test_zero_migration_roundtrip_preserves_guard_owners_acl_and_rls(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_physical_zero_return)")):
                _refuse_without_changes(connection)
                return
            before = connection.execute(FUNCTIONS).all()
            relation = connection.execute(RELATION).one()
            policies = connection.execute(POLICIES).all()
            migration().downgrade()
            assert (
                connection.scalar(text("SELECT to_regclass('public.pos_physical_zero_return')"))
                is None
            )
            migration().upgrade()
            assert connection.execute(FUNCTIONS).all() == before
            assert connection.execute(RELATION).one() == relation
            assert connection.execute(POLICIES).all() == policies

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


async def test_zero_migration_refuses_committed_history(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    marker, _ = await prepared_marker(db_session)
    await PhysicalZeroReturnRepository(db_session).insert(marker)
    await db_session.commit()

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_physical_zero_return)"))
        with Operations.context(MigrationContext.configure(connection)):
            _refuse_without_changes(connection)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
