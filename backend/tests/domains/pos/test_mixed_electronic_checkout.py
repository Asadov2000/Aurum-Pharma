"""One mixed transaction owns legacy stock, physical stock and every payment part."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_electronic_checkout_repository import MixedElectronicCheckoutRepository
from app.domains.pos.mixed_electronic_checkout_service import MixedElectronicCheckoutService
from app.domains.pos.models import POSPaymentAttempt
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_cash_checkout import facts as cash_facts
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_checkout import prepared as physical_prepared
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def service(session: AsyncSession) -> MixedElectronicCheckoutService:
    return MixedElectronicCheckoutService(MixedElectronicCheckoutRepository(session))


async def prepared(
    session: AsyncSession, methods: tuple[str, ...] = ("card",)
) -> tuple[SaveUnitPaymentIntentRequest, dict[str, UUID]]:
    request, identifiers = await physical_prepared(session, methods, mixed=True)
    tenant = runtime(session).stock.tenant_id
    legacy = await UnitCartRepository(session).list_legacy_items(tenant, request.request.sale_id)
    for row in legacy:
        await InventoryRepository(session).insert_movement(
            tenant_id=tenant, batch_id=row.batch_id, movement_type="incoming", qty_delta=row.qty
        )
    return request, {str(method): identifier for method, identifier in identifiers.items()}


async def attempts(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest, ids: dict[str, UUID]
) -> tuple[POSPaymentAttempt, ...]:
    context = runtime(session)
    return await service(session).prepare_attempts(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
        attempt_ids=ids,
    )


async def confirm(
    session: AsyncSession,
    request: SaveUnitPaymentIntentRequest,
    rows: tuple[POSPaymentAttempt, ...],
) -> None:
    context = runtime(session)
    for row in rows:
        await service(session).update_attempt(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            sale_id=request.request.sale_id,
            operation_id=request.operation_id,
            attempt_id=row.id,
            action="reconcile",
        )
        await service(session).update_attempt(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            sale_id=request.request.sale_id,
            operation_id=request.operation_id,
            attempt_id=row.id,
            action="confirm",
            terminal_id="ISOLATED-MIXED",
            external_reference=f"mixed-{row.id}",
        )


async def complete(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest, *, read: bool = False
) -> MixedCheckoutResultV4:
    context = runtime(session)
    command = service(session).read if read else service(session).complete
    return await command(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await cash_facts(session)
    context = runtime(session)
    for table in (
        "pos_unit_payment_attempt_binding",
        "pos_unit_checkout_payment_component",
        "pos_payment_attempt",
    ):
        result[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant ORDER BY id"
                    ),
                    {"tenant": context.stock.tenant_id},
                )
            ).scalars()
        )
    return result


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "qr", "card")])
async def test_mixed_confirmed_plan_posts_both_stock_kinds_and_recovers_once(
    db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    request, source_ids = await prepared(db_session, methods)
    ids = {str(method): identifier for method, identifier in source_ids.items()}
    rows = await attempts(db_session, request, ids)
    assert {row.id for row in rows} == set(ids.values())
    assert await attempts(db_session, request, ids) == rows
    await confirm(db_session, request, rows)
    result = await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    assert {line.inventory_kind for line in result.lines} == {"legacy_pack", "physical_unit"}
    assert result.operation_id == request.operation_id
    assert result.total_amount == request.request.expected_total_amount
    assert {part.method: part.amount for part in result.payments} == {
        part.method: part.amount for part in request.request.payments
    }
    assert {part.method: part.id for part in result.payments} == {
        **ids,
        **({"cash": request.operation_id} if "cash" in methods else {}),
    }
    components = await UnitCheckoutPaymentRepository(db_session).for_checkout(
        result.tenant_id, result.operation_id, mixed=True
    )
    assert {row.id for row in components} == {part.id for part in result.payments}
    assert all(row.physical_checkout_id is None for row in components)
    assert all(row.mixed_checkout_id == request.operation_id for row in components)
    assert {row.payment_attempt_id for row in components if row.method != "cash"} == set(
        ids.values()
    )
    before = await facts(db_session)
    assert await complete(db_session, request, read=True) == result
    assert await complete(db_session, request) == result
    assert await facts(db_session) == before


async def test_pending_set_is_get_only_and_different_uuid_replay_is_rejected(
    db_session: AsyncSession,
) -> None:
    request, source_ids = await prepared(db_session)
    ids = {str(method): identifier for method, identifier in source_ids.items()}
    before = await facts(db_session)
    with pytest.raises(NotFoundError):
        await complete(db_session, request, read=True)
    assert await facts(db_session) == before
    rows = await attempts(db_session, request, ids)
    after = await facts(db_session)
    context = runtime(db_session)
    assert (
        await service(db_session).read_attempts(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            sale_id=request.request.sale_id,
            operation_id=request.operation_id,
        )
        == rows
    )
    with pytest.raises(ConflictError, match="other attempt IDs"):
        await attempts(db_session, request, {method: uuid4() for method in ids})
    with pytest.raises(BusinessRuleError):
        await complete(db_session, request)
    assert await facts(db_session) == after


async def test_voided_attempt_cannot_post_either_stock_kind(db_session: AsyncSession) -> None:
    request, source_ids = await prepared(db_session)
    rows = await attempts(db_session, request, {str(k): v for k, v in source_ids.items()})
    context = runtime(db_session)
    await service(db_session).update_attempt(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
        attempt_id=rows[0].id,
        action="void",
        reason="cashier_cancelled",
        operator_note="isolated operator decision",
        allowed_manage_branch_ids={context.stock.branch_id},
        can_manage_tenant=True,
    )
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_outer_rollback_removes_both_stock_and_all_financial_effects(
    db_session: AsyncSession,
) -> None:
    request, source_ids = await prepared(db_session, ("cash", "card", "qr"))
    rows = await attempts(db_session, request, {str(k): v for k, v in source_ids.items()})
    await confirm(db_session, request, rows)
    before = await facts(db_session)
    async with db_session.begin_nested() as transaction:
        await complete(db_session, request)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await transaction.rollback()
    assert await facts(db_session) == before


async def test_missing_journal_component_rolls_back_legacy_and_physical_together(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, source_ids = await prepared(db_session)
    rows = await attempts(db_session, request, {str(k): v for k, v in source_ids.items()})
    await confirm(db_session, request, rows)
    before = await facts(db_session)

    async def omit(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        assert (
            repository.session is db_session and component.mixed_checkout_id == request.operation_id
        )

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "insert", omit)
    with pytest.raises(DBAPIError):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_recovery_after_closed_shift_does_not_reprice_or_repeat_stock(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, source_ids = await prepared(db_session)
    rows = await attempts(db_session, request, {str(k): v for k, v in source_ids.items()})
    await confirm(db_session, request, rows)
    result = await complete(db_session, request)
    await close_shift(db_session, result.shift_id)
    before = await facts(db_session)

    async def deny_prepare(*args: object, **kwargs: object) -> None:
        raise AssertionError("Saved recovery must not prepare or reprice a cart")

    monkeypatch.setattr(
        "app.domains.pos.mixed_electronic_checkout_service.prepare_electronic_mixed_cart",
        deny_prepare,
    )
    assert await complete(db_session, request, read=True) == result
    assert await complete(db_session, request) == result
    assert await facts(db_session) == before


async def test_foreign_tenant_cannot_read_or_complete_mixed_electronic_history(
    db_session: AsyncSession,
) -> None:
    request, source_ids = await prepared(db_session)
    rows = await attempts(db_session, request, {str(k): v for k, v in source_ids.items()})
    await confirm(db_session, request, rows)
    await complete(db_session, request)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    with pytest.raises(AurumError):
        await complete(db_session, request, read=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("channel", ["prepare", "complete", "read"])
async def test_current_branch_permission_is_required_for_every_mixed_channel(
    db_session: AsyncSession,
    channel: str,
) -> None:
    request, ids = await prepared(db_session)
    if channel != "prepare":
        await confirm(db_session, request, await attempts(db_session, request, ids))
    if channel == "read":
        await complete(db_session, request)
    context = runtime(db_session)
    before = await facts(db_session)
    with pytest.raises(AurumError):
        if channel == "prepare":
            await service(db_session).prepare_attempts(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={uuid4()},
                sale_id=request.request.sale_id,
                operation_id=request.operation_id,
                attempt_ids=ids,
            )
        else:
            command = (
                service(db_session).read if channel == "read" else service(db_session).complete
            )
            await command(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={uuid4()},
                sale_id=request.request.sale_id,
                operation_id=request.operation_id,
            )
    assert await facts(db_session) == before


@pytest.mark.parametrize("damage", ["missing", "amount", "attempt", "parent"])
async def test_saved_mixed_recovery_rejects_incomplete_or_foreign_journal_without_write(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, request, await attempts(db_session, request, ids))
    result = await complete(db_session, request)
    before = await facts(db_session)

    async def corrupt_read(
        repository: UnitCheckoutPaymentRepository,
        tenant_id: UUID,
        checkout_id: UUID,
        *,
        mixed: bool,
    ) -> tuple[UnitCheckoutPaymentComponent, ...]:
        assert tenant_id == result.tenant_id and checkout_id == result.operation_id and mixed
        if damage == "missing":
            return ()
        payment = result.payments[0]
        return (
            UnitCheckoutPaymentComponent(
                tenant_id=tenant_id,
                id=payment.id,
                physical_checkout_id=None,
                mixed_checkout_id=uuid4() if damage == "parent" else checkout_id,
                sale_id=result.sale_id,
                method=payment.method,
                amount=payment.amount + (1 if damage == "amount" else 0),
                currency="TJS",
                payment_attempt_id=uuid4() if damage == "attempt" else payment.id,
                created_by=result.cashier_id,
            ),
        )

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "for_checkout", corrupt_read)
    with pytest.raises(AurumError, match="journal is inconsistent"):
        await complete(db_session, request, read=True)
    assert await facts(db_session) == before


async def test_readonly_tenant_can_recover_completed_mixed_result_without_new_effects(
    db_session: AsyncSession,
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, request, await attempts(db_session, request, ids))
    result = await complete(db_session, request)
    await db_session.execute(
        text("UPDATE tenant SET status='readonly' WHERE id=:tenant"), {"tenant": result.tenant_id}
    )
    before = await facts(db_session)
    assert await complete(db_session, request, read=True) == result
    assert await facts(db_session) == before
