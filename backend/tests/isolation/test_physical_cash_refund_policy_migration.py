"""Policy migration roundtrips preserve ACLs and refuse to discard new history."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_cash_refund_service import PhysicalCashRefundService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.isolation.test_physical_cash_checkout_migration import cash_refund_policy_migration

db_session = receiving_session_fixture

GUARDS = text(
    "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef FROM pg_proc "
    "WHERE pronamespace='public'::regnamespace AND proname IN "
    "('trg_physical_cash_refund_guard','trg_physical_cash_refund_complete',"
    "'trg_guard_sale_immutability') ORDER BY proname"
)
POLICY_COLUMNS = text(
    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
    "AND table_name='pos_physical_cash_refund' "
    "AND column_name IN ('reason_code','comment','review_hash') ORDER BY column_name"
)
HISTORY = text("SELECT to_jsonb(row) FROM pos_physical_cash_refund row ORDER BY tenant_id,id")


def _require_test_database(connection: Connection) -> None:
    assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
    assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"


def _refuse_without_changes(connection: Connection) -> None:
    guards = connection.execute(GUARDS).all()
    history = connection.execute(HISTORY).scalars().all()
    with pytest.raises(DBAPIError) as error:
        with connection.begin_nested():
            cash_refund_policy_migration().downgrade()
    assert getattr(error.value.orig, "sqlstate", None) == "55000"
    assert "physical_cash_refund_policy_history_exists" in str(error.value.orig)
    assert connection.execute(GUARDS).all() == guards
    assert connection.execute(HISTORY).scalars().all() == history
    assert connection.execute(POLICY_COLUMNS).scalars().all() == [
        "comment",
        "reason_code",
        "review_hash",
    ]


async def test_policy_roundtrip_preserves_guard_owner_acl_and_legacy_rows(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        _require_test_database(connection)
        with Operations.context(MigrationContext.configure(connection)):
            if connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pos_physical_cash_refund WHERE "
                    "reason_code IS NOT NULL OR comment IS NOT NULL OR review_hash IS NOT NULL)"
                )
            ):
                _refuse_without_changes(connection)
                return
            before = connection.execute(GUARDS).all()
            history = connection.execute(HISTORY).scalars().all()
            cash_refund_policy_migration().downgrade()
            assert connection.execute(POLICY_COLUMNS).scalars().all() == []
            previous = connection.execute(GUARDS).all()
            assert [(row[0], *row[2:]) for row in previous] == [
                (row[0], *row[2:]) for row in before
            ]
            guard = next(row[1] for row in previous if row[0] == "trg_physical_cash_refund_guard")
            assert "v_reason_mode" not in guard
            assert "NEW.review_hash" not in guard
            cash_refund_policy_migration().upgrade()
            assert connection.execute(GUARDS).all() == before
            assert connection.execute(HISTORY).scalars().all() == history
            assert connection.execute(POLICY_COLUMNS).scalars().all() == [
                "comment",
                "reason_code",
                "review_hash",
            ]

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("history_kind", ["reason", "review"])
async def test_policy_downgrade_refuses_real_committed_new_history(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    history_kind: str,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    original = await complete(db_session, await saved_request(db_session, source.sale))
    selections = (PhysicalReturnSelection(item.id, "unit", 1),)
    result = await PhysicalCashRefundService(PhysicalCashRefundRepository(db_session)).refund(
        tenant_id=original.tenant_id,
        actor_id=original.cashier_id,
        operation_id=uuid4(),
        parent_sale_id=original.sale_id,
        shift_id=original.shift_id,
        selections=selections,
        reason_code="other" if history_kind == "reason" else None,
        comment="Migration policy history" if history_kind == "reason" else None,
        expected_revision_hash=(
            physical_refund_revision(original, {}, selections) if history_kind == "review" else None
        ),
    )
    await db_session.commit()

    def rehearse(connection: Connection) -> None:
        _require_test_database(connection)
        marker = connection.execute(
            text(
                "SELECT reason_code,comment,review_hash FROM pos_physical_cash_refund "
                "WHERE tenant_id=:tenant AND id=:id"
            ),
            {"tenant": result.tenant_id, "id": result.operation_id},
        ).one()
        if history_kind == "reason":
            assert marker.reason_code == "other" and marker.comment == "Migration policy history"
            assert marker.review_hash is None
        else:
            assert marker.reason_code is None and marker.comment is None
            assert marker.review_hash == physical_refund_revision(original, {}, selections)
        with Operations.context(MigrationContext.configure(connection)):
            _refuse_without_changes(connection)

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
