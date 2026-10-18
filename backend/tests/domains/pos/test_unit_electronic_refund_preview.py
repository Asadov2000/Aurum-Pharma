"""Electronic refund previews use immutable real history without issuing money."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, PermissionDeniedError
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_refund_api_schemas import PhysicalRefundQuoteRequest
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.repository import POSRepository
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_electronic_refund_preview import UnitElectronicRefundPreview
from app.domains.pos.unit_electronic_refund_preview_repository import (
    UnitElectronicRefundPreviewRepository,
)
from app.domains.pos.unit_electronic_refund_preview_schemas import (
    ElectronicRefundHistory,
    PaymentFact,
    PhysicalReturnLineFact,
    RefundComponentFact,
    RefundSaleFact,
)
from app.domains.pos.unit_electronic_refund_preview_service import (
    UnitElectronicRefundPreviewService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_checkout_payment_components import complete as complete_cash
from tests.domains.pos.test_unit_checkout_payment_components import request_for as cash_request
from tests.domains.pos.test_unit_electronic_checkout import (
    attempts,
    complete,
    confirm,
    financial_facts,
    prepared,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def original(
    session: AsyncSession, methods: tuple[str, ...] = ("card",)
) -> PhysicalCheckoutResultV3:
    request, ids = await prepared(session, methods)
    await confirm(session, await attempts(session, request, ids))
    return await complete(session, request)


def selection(source: PhysicalCheckoutResultV3, quantity: int = 1) -> PhysicalRefundQuoteRequest:
    return PhysicalRefundQuoteRequest.model_validate(
        {"items": [{"line_id": source.lines[0].id, "sale_format": "unit", "quantity": quantity}]}
    )


async def preview(
    session: AsyncSession,
    source: PhysicalCheckoutResultV3,
    quantity: int = 1,
    *,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branches: set[UUID] | None = None,
) -> UnitElectronicRefundPreview:
    context = runtime(session)
    return await UnitElectronicRefundPreviewService(
        UnitElectronicRefundPreviewRepository(session)
    ).quote(
        tenant_id=tenant_id or context.stock.tenant_id,
        parent_sale_id=source.sale_id,
        actor_id=actor_id or context.actor_id,
        allowed_branch_ids=branches if branches is not None else {context.stock.branch_id},
        request=selection(source, quantity),
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await financial_facts(session)
    tenant = runtime(session).stock.tenant_id
    for table in (
        "pos_physical_return_command",
        "pos_physical_return_line",
        "pos_physical_return_slice",
        "pos_physical_cash_refund",
        "pos_unit_refund_payment_component",
        "pos_refund_attempt",
        "unit_stock_opened_pack_v2",
        "unit_stock_cart_line_v2",
    ):
        rows = await session.execute(
            text(
                f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                "ORDER BY to_jsonb(row)::text"
            ),
            {"tenant": tenant},
        )
        result[table] = list(rows.scalars())
    return result


def allocations(result: UnitElectronicRefundPreview) -> dict[str, Decimal]:
    return {part.method: part.amount for part in result.payments}


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "card", "qr")])
async def test_exact_net_allocation_and_full_return_close_every_original_payment(
    db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    source = await original(db_session, methods)
    before = await facts(db_session)
    first = await preview(db_session, source)
    assert first.state == "preview"
    assert first.parent_sale_id == source.sale_id and first.branch_id == source.branch_id
    assert first.original_receipt_number == source.receipt_number
    assert first.total_amount == Decimal("0.99")
    expected = (
        {"card": Decimal("0.49"), "cash": Decimal("0.24"), "qr": Decimal("0.26")}
        if len(methods) == 3
        else {methods[0]: Decimal("0.99")}
    )
    assert allocations(first) == expected
    assert first.lines[0].offset == 0 and first.lines[0].base_units == 1
    assert first.lines[0].net_amount == first.total_amount
    assert all(part.currency == "TJS" for part in first.payments)
    sources = {part.method: part.id for part in source.payments}
    assert {part.method: part.original_payment_id for part in first.payments} == sources
    assert await preview(db_session, source) == first
    full = await preview(db_session, source, 2)
    assert full.total_amount == source.total_amount == Decimal("1.99")
    assert allocations(full) == {part.method: part.amount for part in source.payments}
    assert full.revision_hash != first.revision_hash
    assert await facts(db_session) == before


@pytest.mark.parametrize("mutation", ["DELETE", "INSERT"])
async def test_refund_only_different_actor_reads_scoped_payment_history_without_writes(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    mutation: str,
) -> None:
    source = await original(db_session, ("cash", "card", "qr"))
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    assert actor != source.cashier_id
    await set_receiving_context(db_session, context.stock, actor)
    before = await facts(db_session)
    result = await preview(db_session, source, actor_id=actor)
    assert result.total_amount == Decimal("0.99")
    assert len(result.payments) == 3
    assert await preview(db_session, source, actor_id=actor) == result
    assert await facts(db_session) == before
    component = source.payments[0]
    statement = (
        "DELETE FROM pos_unit_checkout_payment_component WHERE id=:id"
        if mutation == "DELETE"
        else "INSERT INTO pos_unit_checkout_payment_component SELECT * FROM "
        "pos_unit_checkout_payment_component WHERE id=:id"
    )
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement),
                {"id": component.id},
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await facts(db_session) == before


@pytest.mark.parametrize("mode", ["closed_shift", "readonly"])
async def test_preview_is_read_only_after_original_shift_or_tenant_closes(
    db_session: AsyncSession, db_engine: AsyncEngine, mode: str
) -> None:
    source = await original(db_session)
    baseline = await preview(db_session, source)
    if mode == "closed_shift":
        await close_shift(db_session, source.shift_id)
    else:
        await db_session.commit()
        context = runtime(db_session)
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:id"),
                {"id": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    assert await preview(db_session, source) == baseline
    assert await facts(db_session) == before


@pytest.mark.parametrize("damage", ["scope", "tenant_context", "actor_context"])
async def test_current_authority_is_required_even_for_read_only_preview(
    db_session: AsyncSession, damage: str
) -> None:
    source = await original(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    if damage == "tenant_context":
        await db_session.execute(
            text("SELECT set_config('app.tenant_id',:id,true)"), {"id": str(uuid4())}
        )
    elif damage == "actor_context":
        await db_session.execute(
            text("SELECT set_config('app.user_id',:id,true)"), {"id": str(uuid4())}
        )
    with pytest.raises(AurumError):
        await preview(db_session, source, branches={uuid4()} if damage == "scope" else None)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("damage", ["payment_missing", "component_missing", "payment_amount"])
async def test_incomplete_or_mismatched_original_payment_history_fails_closed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    source = await original(db_session)
    before = await facts(db_session)
    read = UnitElectronicRefundPreviewRepository.read

    async def corrupt(
        repository: UnitElectronicRefundPreviewRepository, tenant_id: UUID, parent_sale_id: UUID
    ) -> ElectronicRefundHistory | None:
        history = await read(repository, tenant_id, parent_sale_id)
        assert history is not None
        if damage == "payment_missing":
            return history.model_copy(update={"payments": ()})
        if damage == "component_missing":
            return history.model_copy(update={"components": ()})
        payment = history.payments[0].model_copy(
            update={"amount": history.payments[0].amount + Decimal("0.01")}
        )
        return history.model_copy(update={"payments": (payment,)})

    monkeypatch.setattr(UnitElectronicRefundPreviewRepository, "read", corrupt)
    with pytest.raises(AurumError):
        await preview(db_session, source)
    assert await facts(db_session) == before


async def test_goods_return_without_matching_payout_history_fails_closed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await original(db_session)
    before = await facts(db_session)
    read = UnitElectronicRefundPreviewRepository.read

    async def unmatched_goods(
        repository: UnitElectronicRefundPreviewRepository, tenant_id: UUID, parent_sale_id: UUID
    ) -> ElectronicRefundHistory | None:
        history = await read(repository, tenant_id, parent_sale_id)
        assert history is not None
        line = PhysicalReturnLineFact(
            tenant_id=source.tenant_id,
            command_id=uuid4(),
            line_id=source.lines[0].id,
            stock_command_id=source.stock_command_id,
            branch_id=source.branch_id,
            offset=0,
            base_units=1,
            net_amount=Decimal("0.99"),
        )
        return history.model_copy(update={"return_lines": (line,)})

    monkeypatch.setattr(UnitElectronicRefundPreviewRepository, "read", unmatched_goods)
    with pytest.raises(AurumError):
        await preview(db_session, source)
    assert await facts(db_session) == before


@pytest.mark.parametrize("version", [1, 2])
async def test_cash_checkout_is_outside_electronic_refund_preview(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    request = await cash_request(db_session, False)
    insert = PhysicalCashCheckoutRepository.insert

    async def old_marker(
        repository: PhysicalCashCheckoutRepository, marker: PhysicalCashCheckout
    ) -> None:
        marker.payment_journal_version = 1
        await insert(repository, marker)

    async def no_component(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    with monkeypatch.context() as legacy:
        if version == 1:
            legacy.setattr(PhysicalCashCheckoutRepository, "insert", old_marker)
            legacy.setattr(UnitCheckoutPaymentRepository, "insert", no_component)
        source = await complete_cash(db_session, request, False)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await preview(db_session, source)
    assert await facts(db_session) == before


async def test_uncompleted_checkout_cannot_be_previewed(db_session: AsyncSession) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    context = runtime(db_session)
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await UnitElectronicRefundPreviewService(
            UnitElectronicRefundPreviewRepository(db_session)
        ).quote(
            tenant_id=context.stock.tenant_id,
            parent_sale_id=request.request.sale_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            request=PhysicalRefundQuoteRequest.model_validate(
                {"items": [{"line_id": uuid4(), "sale_format": "unit", "quantity": 1}]}
            ),
        )
    assert await facts(db_session) == before


async def test_preview_preserves_pending_orm_edits_without_autoflush(
    db_session: AsyncSession,
) -> None:
    source = await original(db_session)
    sale = await POSRepository(db_session).get_sale(source.sale_id)
    assert sale is not None
    before = await facts(db_session)
    sale.total_amount = source.total_amount + Decimal("0.01")
    result = await preview(db_session, source)
    assert result.total_amount == Decimal("0.99")
    assert sale in db_session.dirty
    assert sale.total_amount == source.total_amount + Decimal("0.01")
    db_session.expire(sale, ["total_amount"])
    assert await facts(db_session) == before


def after_first_partial(history: ElectronicRefundHistory) -> ElectronicRefundHistory:
    source = history.checkout.result_payload
    refund_id, command_id = uuid4(), uuid4()
    amounts = {"card": Decimal("0.49"), "cash": Decimal("0.24"), "qr": Decimal("0.26")}
    payouts: list[PaymentFact] = []
    components: list[RefundComponentFact] = []
    for payment in source.payments:
        payment_id = uuid4()
        payouts.append(
            PaymentFact(
                id=payment_id,
                tenant_id=source.tenant_id,
                sale_id=refund_id,
                payment_method=payment.method,
                amount=amounts[payment.method],
                currency="TJS",
                operation_id=payment_id,
                operation_hash="0" * 64,
                payment_attempt_id=None,
                metadata=None,
            )
        )
        components.append(
            RefundComponentFact(
                id=payment_id,
                tenant_id=source.tenant_id,
                parent_sale_id=source.sale_id,
                sale_id=refund_id,
                original_payment_id=payment.id,
                physical_refund_id=command_id,
                mixed_refund_id=None,
                method=payment.method,
                amount=amounts[payment.method],
                currency="TJS",
                created_by=source.cashier_id,
            )
        )
    line = PhysicalReturnLineFact(
        tenant_id=source.tenant_id,
        command_id=command_id,
        line_id=source.lines[0].id,
        stock_command_id=source.stock_command_id,
        branch_id=source.branch_id,
        offset=0,
        base_units=1,
        net_amount=Decimal("0.99"),
    )
    returned = RefundSaleFact(
        id=refund_id,
        tenant_id=source.tenant_id,
        branch_id=source.branch_id,
        parent_sale_id=source.sale_id,
        status="completed",
        sale_type="return",
        currency="TJS",
        is_test=False,
        total_amount=Decimal("0.99"),
        cashier_user_id=source.cashier_id,
    )
    return history.model_copy(
        update={
            "return_lines": (line,),
            "refund_sales": (returned,),
            "payouts": tuple(payouts),
            "refund_components": tuple(components),
        }
    )


@pytest.mark.parametrize("damage", [None, "missing_payout", "wrong_original", "wrong_net"])
async def test_remaining_cent_uses_reconciled_goods_and_actual_payment_history(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    source = await original(db_session, ("cash", "card", "qr"))
    before = await facts(db_session)
    saved_history = await UnitElectronicRefundPreviewRepository(db_session).read(
        source.tenant_id, source.sale_id
    )
    assert saved_history is not None
    prior = after_first_partial(saved_history)

    async def prior_refund(
        repository: UnitElectronicRefundPreviewRepository, tenant_id: UUID, parent_sale_id: UUID
    ) -> ElectronicRefundHistory | None:
        assert repository.session is db_session
        assert (tenant_id, parent_sale_id) == (source.tenant_id, source.sale_id)
        # Project a reconciled prior refund without enabling any payout writer.
        history = prior
        if damage == "missing_payout":
            return history.model_copy(update={"payouts": history.payouts[:-1]})
        if damage == "wrong_original":
            part = history.refund_components[0].model_copy(update={"original_payment_id": uuid4()})
            return history.model_copy(
                update={"refund_components": (part, *history.refund_components[1:])}
            )
        if damage == "wrong_net":
            line = history.return_lines[0].model_copy(update={"net_amount": Decimal("1.00")})
            return history.model_copy(update={"return_lines": (line,)})
        return history

    monkeypatch.setattr(UnitElectronicRefundPreviewRepository, "read", prior_refund)
    if damage is None:
        result = await preview(db_session, source)
        assert result.lines[0].offset == 1
        assert result.total_amount == Decimal("1.00")
        assert allocations(result) == {
            "card": Decimal("0.51"),
            "cash": Decimal("0.25"),
            "qr": Decimal("0.24"),
        }
        assert await preview(db_session, source) == result
    else:
        with pytest.raises(AurumError):
            await preview(db_session, source)
    assert await facts(db_session) == before


async def test_blocking_real_actor_revokes_preview_without_financial_effects(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await original(db_session)
    context = runtime(db_session)
    assert str(await db_session.scalar(text("SELECT current_database()"))).endswith("_test")
    assert (await preview(db_session, source)).total_amount == Decimal("0.99")
    before = await facts(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE app_user SET status='blocked' WHERE id=:id"), {"id": context.actor_id}
        )
        assert (
            await owner.scalar(
                text("SELECT status FROM app_user WHERE id=:id"), {"id": context.actor_id}
            )
            == "blocked"
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        with pytest.raises(PermissionDeniedError):
            await preview(db_session, source)
        await db_session.rollback()
    finally:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='active' WHERE id=:id"), {"id": context.actor_id}
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
