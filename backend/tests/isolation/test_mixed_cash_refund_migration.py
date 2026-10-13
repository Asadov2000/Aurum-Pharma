"""Mixed payout markers prove quarantine, cash and immutable receipt atomically."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.domains.pos.mixed_cash_refund_models import MixedCashRefund
from app.domains.pos.mixed_cash_refund_repository import MixedCashRefundRepository
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_cash_refund_schemas import (
    MixedCashRefundReceiptV4,
    MixedCashRefundResultV4,
)
from app.domains.pos.mixed_cash_refund_service import MixedCashRefundService
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.models import Sale
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext
from tests.domains.pos.test_mixed_return_journal import MixedSource, mixed_source
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_physical_cash_checkout_migration import (
    mixed_cash_refund_migration,
    mixed_quarantine_disposition_migration,
    mixed_quarantine_read_migration,
    mixed_zero_return_migration,
    physical_disposition_migration,
)

db_session = receiving_session_fixture
TABLES = (
    "pos_mixed_cash_refund",
    "pos_mixed_return_command",
    "pos_mixed_return_line",
    "pos_mixed_return_slice",
)


async def refund(
    session: AsyncSession,
    source: MixedSource,
    repository: MixedCashRefundRepository | None = None,
    *,
    operation_id: UUID | None = None,
    expected_revision_hash: str | None = None,
) -> MixedCashRefundResultV4:
    return await MixedCashRefundService(repository or MixedCashRefundRepository(session)).refund(
        tenant_id=source.original.tenant_id,
        actor_id=runtime(session).actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=source.original.sale_id,
        shift_id=source.original.shift_id,
        selections=source.selections(),
        expected_revision_hash=expected_revision_hash,
    )


async def counts(session: AsyncSession) -> tuple[int, ...]:
    values: list[int] = []
    for table in TABLES:
        values.append(int(await session.scalar(text(f"SELECT count(*) FROM {table}")) or 0))
    return tuple(values)


class ForgedRefundRepository(MixedCashRefundRepository):
    def __init__(self, session: AsyncSession, corruption: str) -> None:
        super().__init__(session)
        self.corruption = corruption

    # Keep each deliberately corrupted DB fact visible for the parametrized cases.
    async def insert(self, marker: MixedCashRefund) -> None:
        if self.corruption == "receipt":
            marker.receipt_payload = {**marker.receipt_payload, "total_amount": "999.00"}
        elif self.corruption == "receipt_lines":
            marker.receipt_payload = {**marker.receipt_payload, "lines": []}
        elif self.corruption == "result":
            marker.result_payload = {**marker.result_payload, "total_amount": "999.00"}
        elif self.corruption == "physical_cost":
            marker.result_payload = {**marker.result_payload, "physical_cost_amount": "999.00"}
        elif self.corruption == "hash":
            marker.request_hash = "0" * 64
        elif self.corruption == "parent":
            marker.parent_sale_id = uuid4()
        elif self.corruption == "payment":
            marker.payment_id = uuid4()
        elif self.corruption == "actor":
            marker.actor_id = uuid4()
        elif self.corruption == "branch":
            marker.branch_id = uuid4()
        elif self.corruption == "shift":
            marker.shift_id = uuid4()
        elif self.corruption == "review":
            marker.review_hash = "0" * 64
        await super().insert(marker)
        if self.corruption == "marker_only":
            await self.validate_completion()

    async def create_sale(self, sale: Sale) -> None:
        if self.corruption == "sale_total":
            sale.total_amount += 1
        elif self.corruption == "sale_hash":
            sale.operation_hash = "0" * 64
        await super().create_sale(sale)

    async def insert_payment(
        self, *, tenant_id: UUID, sale_id: UUID, payment_id: UUID, amount: Decimal
    ) -> None:
        if self.corruption == "missing_payment":
            return
        if self.corruption == "payment_amount":
            amount += 1
        await super().insert_payment(
            tenant_id=tenant_id, sale_id=sale_id, payment_id=payment_id, amount=amount
        )
        if self.corruption == "extra_payment":
            await super().insert_payment(
                tenant_id=tenant_id, sale_id=sale_id, payment_id=uuid4(), amount=amount
            )

    async def complete_sale(self, sale: Sale, receipt: MixedCashRefundReceiptV4) -> None:
        if self.corruption == "draft":
            return
        await super().complete_sale(sale, receipt)


@pytest.mark.parametrize(
    "corruption",
    [
        "receipt",
        "receipt_lines",
        "result",
        "physical_cost",
        "hash",
        "parent",
        "payment",
        "actor",
        "branch",
        "shift",
        "review",
        "marker_only",
        "sale_total",
        "sale_hash",
        "missing_payment",
        "payment_amount",
        "extra_payment",
        "draft",
    ],
)
async def test_direct_tamper_rolls_back_entire_mixed_refund(
    db_session: AsyncSession, corruption: str
) -> None:
    source = await mixed_source(db_session)
    operation_id = uuid4()
    with pytest.raises(DBAPIError):
        await refund(
            db_session,
            source,
            ForgedRefundRepository(db_session, corruption),
            operation_id=operation_id,
        )
    assert await counts(db_session) == (0, 0, 0, 0)
    assert not await db_session.scalar(
        text("SELECT EXISTS(SELECT 1 FROM sale WHERE parent_sale_id=:id)"),
        {"id": source.original.sale_id},
    )
    result = await refund(db_session, source, operation_id=operation_id)
    assert result.operation_id == operation_id
    assert await counts(db_session) == (1, 1, 2, 1)


class ForbiddenComponentRepository(MixedCashRefundRepository):
    def __init__(self, session: AsyncSession, component: str) -> None:
        super().__init__(session)
        self.component = component

    async def create_sale(self, sale: Sale) -> None:
        await super().create_sale(sale)
        statements = {
            "sale_item": (
                "INSERT INTO sale_item SELECT (jsonb_populate_record(NULL::sale_item,"
                "to_jsonb(item)||jsonb_build_object('id',gen_random_uuid(),'sale_id',"
                "CAST(:sale AS uuid),'parent_sale_item_id',item.id))).* "
                "FROM sale_item item WHERE item.sale_id=:parent"
            ),
            "pos_unit_cart_item": (
                "INSERT INTO pos_unit_cart_item SELECT (jsonb_populate_record("
                "NULL::pos_unit_cart_item,to_jsonb(item)||jsonb_build_object("
                "'id',gen_random_uuid(),'sale_id',CAST(:sale AS uuid)))).* "
                "FROM pos_unit_cart_item item WHERE item.sale_id=:parent"
            ),
            "batch_movement": (
                "INSERT INTO batch_movement SELECT (jsonb_populate_record(NULL::batch_movement,"
                "to_jsonb(item)||jsonb_build_object('id',gen_random_uuid(),"
                "'source_id',CAST(:sale AS uuid),'movement_type','sale_return',"
                "'qty_delta',1,'operation_key',CAST(:sale AS text)))).* "
                "FROM batch_movement item WHERE item.source_table='sale' AND item.source_id=:parent"
            ),
            "unit_stock_cart_command_v2": (
                "INSERT INTO unit_stock_cart_command_v2 SELECT (jsonb_populate_record("
                "NULL::unit_stock_cart_command_v2,to_jsonb(item)||jsonb_build_object("
                "'id',CAST(:operation AS uuid),'checkout_sale_id',NULL,"
                "'mixed_checkout_sale_id',CAST(:sale AS uuid)))).* "
                "FROM unit_stock_cart_command_v2 item WHERE item.mixed_checkout_sale_id=:parent"
            ),
        }
        await self.session.execute(
            text(statements[self.component]),
            {"sale": sale.id, "parent": sale.parent_sale_id, "operation": sale.operation_id},
        )


@pytest.mark.parametrize(
    "component", ["sale_item", "pos_unit_cart_item", "batch_movement", "unit_stock_cart_command_v2"]
)
async def test_draft_mixed_refund_rejects_legacy_items_and_all_stock_posting(
    db_session: AsyncSession, component: str
) -> None:
    source = await mixed_source(db_session)
    with pytest.raises(DBAPIError, match="mixed_cash_refund_components_forbidden"):
        await refund(db_session, source, ForbiddenComponentRepository(db_session, component))
    assert await counts(db_session) == (0, 0, 0, 0)


async def test_review_hash_validates_all_previous_cursors_and_nested_writer_identity(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    await refund(db_session, source)
    journal = MixedReturnJournalRepository(db_session)
    legacy, physical = await journal.cursors(source.original.tenant_id, source.original.sale_id)
    revision = mixed_refund_revision(source.original, legacy, physical, source.selections())
    async with db_session.begin_nested():
        result = await refund(db_session, source, expected_revision_hash=revision)
        assert await db_session.scalar(
            text(
                "SELECT cash_refund_writer_xid=pg_current_xact_id() "
                "FROM pos_mixed_return_command WHERE id=:id"
            ),
            {"id": result.operation_id},
        )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await counts(db_session) == (2, 2, 4, 2)


class CaptureMarkerRepository(MixedCashRefundRepository):
    marker: MixedCashRefund | None = None

    async def insert(self, marker: MixedCashRefund) -> None:
        self.marker = marker
        raise RuntimeError("capture marker before persistence")


async def test_committed_journal_cannot_be_promoted_by_direct_marker_insert(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    operation_id = uuid4()
    capture = CaptureMarkerRepository(db_session)
    with pytest.raises(RuntimeError, match="capture marker"):
        await refund(db_session, source, capture, operation_id=operation_id)
    assert capture.marker is not None
    await MixedReturnJournalService(MixedReturnJournalRepository(db_session)).receive(
        tenant_id=source.original.tenant_id,
        actor_id=context.actor_id,
        operation_id=operation_id,
        parent_sale_id=source.original.sale_id,
        selections=source.selections(),
    )
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(DBAPIError, match="mixed_cash_refund_scope_invalid"):
        async with db_session.begin_nested():
            await MixedCashRefundRepository(db_session).insert(capture.marker)
    assert await counts(db_session) == (0, 1, 2, 1)


async def test_binding_finance_and_receipt_remain_immutable_and_audit_is_redacted(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    result = await refund(db_session, source)
    for statement, identifier in (
        (
            "UPDATE pos_mixed_cash_refund SET request_hash=request_hash WHERE id=:id",
            result.operation_id,
        ),
        ("DELETE FROM pos_mixed_cash_refund WHERE id=:id", result.operation_id),
        ("UPDATE sale SET total_amount=total_amount+1 WHERE id=:id", result.sale_id),
        ("UPDATE sale SET receipt_snapshot='{}'::jsonb WHERE id=:id", result.sale_id),
        ("DELETE FROM sale WHERE id=:id", result.sale_id),
        ("UPDATE sale_payment SET amount=amount+1 WHERE id=:id", result.payment_id),
        ("DELETE FROM sale_payment WHERE id=:id", result.payment_id),
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": identifier})
    snapshots = (
        await db_session.execute(
            text(
                "SELECT new_values->>'result_payload',new_values->>'receipt_payload',"
                "new_values->>'comment' FROM audit_log WHERE table_name='pos_mixed_cash_refund' "
                "AND record_id=:id AND tenant_id=:tenant AND action='INSERT'"
            ),
            {"id": result.operation_id, "tenant": result.tenant_id},
        )
    ).all()
    assert snapshots and all(tuple(snapshot) == ("***", "***", "***") for snapshot in snapshots)
    assert await db_session.scalar(
        text(
            "SELECT cost_amount IS NULL FROM pos_mixed_return_line "
            "WHERE command_id=:id AND inventory_kind='legacy_pack'"
        ),
        {"id": result.operation_id},
    )


async def test_direct_mixed_return_without_marker_remains_forbidden(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    with pytest.raises(DBAPIError, match="mixed_checkout_refund_not_supported"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO sale(tenant_id,id,branch_id,register_id,shift_id,cashier_user_id,"
                    "sale_type,parent_sale_id,status,is_test,total_amount,currency) "
                    "SELECT tenant_id,:id,branch_id,register_id,shift_id,cashier_user_id,"
                    "'return',id,'draft',false,1,'TJS' FROM sale WHERE id=:parent"
                ),
                {"id": uuid4(), "parent": source.original.sale_id},
            )


async def test_mixed_cash_marker_rls_hides_other_branch_and_tenant_and_support_cannot_write(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    result = await refund(db_session, source)
    # Release tenant-access shared locks before another connection creates a branch.
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        with pytest.raises(DBAPIError):
            async with support.begin_nested():
                await support.execute(
                    text("INSERT INTO pos_mixed_cash_refund SELECT * FROM pos_mixed_cash_refund"),
                )
        branch_id = await support.scalar(
            text(
                "INSERT INTO branch(tenant_id,name) "
                "VALUES(:tenant,'Mixed payout scope') RETURNING id"
            ),
            {"tenant": context.stock.tenant_id},
        )
    assert isinstance(branch_id, UUID)
    foreign = LedgerContext(context.stock.tenant_id, branch_id, context.stock.catalog_id)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_cash_refund")) == 0
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_mixed_cash_refund(tenant_id,id,parent_sale_id,sale_id,"
                    "branch_id,register_id,shift_id,actor_id,payment_id,request_hash,"
                    "result_payload,receipt_payload,created_by,updated_by) "
                    "VALUES(:tenant,:id,:parent,:sale,:branch,:register,:shift,:actor,:id,"
                    ":hash,'{}'::jsonb,'{}'::jsonb,:actor,:actor)"
                ),
                {
                    "tenant": result.tenant_id,
                    "id": uuid4(),
                    "parent": result.parent_sale_id,
                    "sale": uuid4(),
                    "branch": result.branch_id,
                    "register": result.register_id,
                    "shift": result.shift_id,
                    "actor": actor,
                    "hash": "0" * 64,
                },
            )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_cash_refund")) == 0


async def test_disposable_roundtrip_restores_historical_guards_and_acl_or_refuses_history(
    maintenance_engine: AsyncEngine,
) -> None:
    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        functions = text(
            "SELECT proname,pg_get_functiondef(oid),proowner,proacl,prosecdef,proconfig "
            "FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN "
            "('trg_mixed_cash_refund_guard','trg_mixed_cash_refund_complete',"
            "'trg_mixed_zero_return_guard',"
            "'trg_mixed_cash_refund_components','trg_mixed_return_guard',"
            "'trg_mixed_checkout_return_boundary','trg_guard_sale_immutability',"
            "'audit_redact_table_snapshot') ORDER BY proname"
        )
        relation = text(
            "SELECT relowner,relacl,relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='pos_mixed_cash_refund'::regclass"
        )
        policies = text(
            "SELECT policyname,roles,cmd,qual,with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename='pos_mixed_cash_refund' ORDER BY policyname"
        )
        with Operations.context(MigrationContext.configure(connection)):
            before = connection.execute(functions).all()
            original_relation = connection.execute(relation).one()
            original_policies = connection.execute(policies).all()
            if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_mixed_zero_return)")):
                with pytest.raises(DBAPIError, match="mixed_zero_return_history_exists") as error:
                    with connection.begin_nested():
                        mixed_zero_return_migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
            elif connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pos_mixed_cash_refund)")):
                with pytest.raises(DBAPIError, match="mixed_cash_refund_history_exists") as error:
                    with connection.begin_nested():
                        mixed_cash_refund_migration().downgrade()
                assert getattr(error.value.orig, "sqlstate", None) == "55000"
            else:
                # Keep physical history; suspend only the later audit insertion.
                physical = physical_disposition_migration()
                physical._redactor(physical.AUDIT_NEW, physical.AUDIT_OLD)
                mixed_quarantine_disposition_migration().downgrade()
                mixed_quarantine_read_migration().downgrade()
                mixed_zero_return_migration().downgrade()
                mixed_cash_refund_migration().downgrade()
                assert (
                    connection.scalar(text("SELECT to_regclass('pos_mixed_cash_refund')")) is None
                )
                assert not connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='pos_mixed_return_command' "
                        "AND column_name='cash_refund_writer_xid')"
                    )
                )
                mixed_cash_refund_migration().upgrade()
                mixed_zero_return_migration().upgrade()
                mixed_quarantine_read_migration().upgrade()
                mixed_quarantine_disposition_migration().upgrade()
                physical._redactor(physical.AUDIT_OLD, physical.AUDIT_NEW)
            assert connection.execute(functions).all() == before
            assert connection.execute(relation).one() == original_relation
            assert connection.execute(policies).all() == original_policies
            assert connection.scalar(
                text(
                    "SELECT bool_and(c.condeferrable AND c.condeferred) FROM pg_constraint c "
                    "JOIN pg_attribute tenant ON tenant.attrelid=c.conrelid "
                    "AND tenant.attname='tenant_id' "
                    "JOIN pg_attribute sale ON sale.attrelid=c.conrelid AND sale.attname='sale_id' "
                    "WHERE c.conrelid='pos_mixed_cash_refund'::regclass AND c.contype='f' "
                    "AND c.confrelid='sale'::regclass AND c.conkey=ARRAY[tenant.attnum,sale.attnum]"
                )
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
