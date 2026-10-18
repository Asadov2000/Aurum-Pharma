"""Internal electronic checkout binds exact attempts without exposing terminal writes."""

from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError
from app.domains.pos.models import POSPaymentAttempt
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_electronic_checkout_service import UnitElectronicCheckoutService
from app.domains.pos.unit_payment_attempt_models import UnitPaymentAttemptBinding
from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from app.domains.pos.unit_payment_intent_service import payment_intent_request_hash
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import receive_source
from tests.domains.pos.test_payment_configuration import _set_payment_settings
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_cart_quote import quote
from tests.domains.pos.test_unit_checkout_payment_components import journal
from tests.domains.pos.test_unit_payment_binding import binding
from tests.domains.pos.test_unit_payment_intent import intent_request, save
from tests.domains.pos.test_unit_payment_plan import snapshot
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
ElectronicMethod = Literal["card", "qr"]


async def prepared(
    session: AsyncSession, methods: tuple[str, ...] = ("card",), *, mixed: bool = False
) -> tuple[SaveUnitPaymentIntentRequest, dict[ElectronicMethod, UUID]]:
    context = runtime(session)
    await _set_payment_settings(
        session, tenant_id=context.stock.tenant_id, methods=["cash", "card", "qr"], mixed=True
    )
    await receive_source(session)
    request = await intent_request(session, mixed=mixed)
    total = request.request.expected_total_amount
    amounts = (
        {"cash": Decimal("0.49"), "card": Decimal("1.00"), "qr": total - Decimal("1.49")}
        if len(methods) == 3
        else {methods[0]: total}
    )
    request = SaveUnitPaymentIntentRequest.model_validate(
        {
            **request.model_dump(),
            "request": {
                **request.request.model_dump(),
                "payments": [{"method": method, "amount": amounts[method]} for method in methods],
                "cash_tendered": "5.00" if "cash" in methods else None,
            },
        }
    )
    await save(session, request)
    await binding(session, request, "bind")
    ids: dict[ElectronicMethod, UUID] = {}
    for part in request.request.payments:
        if part.method in {"card", "qr"}:
            method: ElectronicMethod = "card" if part.method == "card" else "qr"
            ids[method] = uuid4()
    return request, ids


async def attempts(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest, ids: dict[ElectronicMethod, UUID]
) -> tuple[POSPaymentAttempt, ...]:
    context = runtime(session)
    return await UnitElectronicCheckoutService(
        UnitElectronicCheckoutRepository(session)
    ).prepare_attempts(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
        attempt_ids=ids,
    )


async def confirm(session: AsyncSession, rows: tuple[POSPaymentAttempt, ...]) -> None:
    context = runtime(session)
    service = POSService(POSRepository(session))
    for row in rows:
        await service.begin_payment_attempt_reconciliation(
            tenant_id=context.stock.tenant_id, attempt_id=row.id, actor_id=context.actor_id
        )
        await service.confirm_payment_attempt(
            tenant_id=context.stock.tenant_id,
            attempt_id=row.id,
            actor_id=context.actor_id,
            terminal_id="TEST-TERMINAL",
            external_reference=f"test-{row.id}",
        )


async def complete(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest, *, read: bool = False
) -> PhysicalCheckoutResultV3:
    context = runtime(session)
    service = UnitElectronicCheckoutService(UnitElectronicCheckoutRepository(session))
    method = service.read if read else service.complete
    return await method(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
    )


async def financial_facts(session: AsyncSession) -> dict[str, list[object]]:
    facts = await snapshot(session)
    tenant = runtime(session).stock.tenant_id
    for table in (
        "pos_physical_checkout",
        "pos_unit_checkout_payment_component",
        "pos_unit_payment_attempt_binding",
    ):
        result = await session.execute(
            text(f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant ORDER BY id"),
            {"tenant": tenant},
        )
        facts[table] = list(result.scalars())
    return facts


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("qr", "cash", "card")])
async def test_exact_confirmed_plan_posts_once_and_recovers_without_more_effects(
    db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    request, ids = await prepared(db_session, methods)
    rows = await attempts(db_session, request, ids)
    assert {row.id for row in rows} == set(ids.values())
    assert all(row.status == "pending" for row in rows)
    assert await attempts(db_session, request, ids) == rows
    await confirm(db_session, rows)
    result = await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    assert result.operation_id == request.operation_id
    assert result.sale_id == request.request.sale_id
    assert result.total_amount == request.request.expected_total_amount
    assert [part.method for part in result.payments] == sorted(methods)
    assert {part.method: part.amount for part in result.payments} == {
        part.method: part.amount for part in request.request.payments
    }
    assert {part.method: part.id for part in result.payments} == {
        **ids,
        **({"cash": request.operation_id} if "cash" in methods else {}),
    }
    marker = (
        await db_session.execute(
            text(
                "SELECT payment_journal_version,cash_intent_id,payment_plan_intent_id "
                "FROM pos_physical_checkout WHERE id=:id"
            ),
            {"id": request.operation_id},
        )
    ).one()
    assert marker.payment_journal_version == 3
    assert marker.cash_intent_id is None and marker.payment_plan_intent_id == request.operation_id
    components = await UnitCheckoutPaymentRepository(db_session).for_checkout(
        result.tenant_id, result.operation_id, mixed=False
    )
    assert len(components) == len(methods)
    assert {row.id for row in components} == {part.id for part in result.payments}
    electronic_ids = {row.payment_attempt_id for row in components if row.method != "cash"}
    assert electronic_ids == set(ids.values())
    states = await db_session.execute(
        text("SELECT id,status,consumed_at FROM pos_payment_attempt WHERE sale_id=:sale"),
        {"sale": result.sale_id},
    )
    assert all(row.status == "consumed" and row.consumed_at is not None for row in states)
    payment_rows = await db_session.execute(
        text(
            "SELECT id,payment_method,operation_id,payment_attempt_id,operation_hash "
            "FROM sale_payment WHERE sale_id=:sale"
        ),
        {"sale": result.sale_id},
    )
    persisted = payment_rows.all()
    assert len(persisted) == len(methods)
    assert len({row.operation_id for row in persisted}) == len(persisted)
    for payment in persisted:
        assert payment.operation_hash == payment_intent_request_hash(request.request)
        if payment.payment_method == "cash":
            assert payment.id == payment.operation_id == request.operation_id
            assert payment.payment_attempt_id is None
        else:
            assert payment.id == payment.payment_attempt_id == ids[payment.payment_method]
            assert isinstance(payment.operation_id, UUID) and payment.operation_id.version == 4
            assert payment.operation_id not in {*ids.values(), request.operation_id}
    before = await financial_facts(db_session)
    assert await complete(db_session, request) == result
    assert await complete(db_session, request, read=True) == result
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("state", ["pending", "reconciling", "partly_confirmed"])
async def test_nonconfirmed_plan_never_posts_stock_payments_or_marker(
    db_session: AsyncSession, state: str
) -> None:
    request, ids = await prepared(db_session, ("cash", "card", "qr"))
    rows = await attempts(db_session, request, ids)
    if state == "reconciling":
        context = runtime(db_session)
        await POSService(POSRepository(db_session)).begin_payment_attempt_reconciliation(
            tenant_id=context.stock.tenant_id, attempt_id=rows[0].id, actor_id=context.actor_id
        )
    elif state == "partly_confirmed":
        await confirm(db_session, rows[:1])
    before = await financial_facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError)):
        await complete(db_session, request)
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("damage", ["missing", "extra", "changed"])
async def test_attempt_identity_set_is_exact_and_cannot_be_rebound(
    db_session: AsyncSession, damage: str
) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    changed: dict[ElectronicMethod, UUID] = dict(ids)
    if damage == "missing":
        changed.clear()
    elif damage == "extra":
        changed["qr"] = uuid4()
    else:
        changed["card"] = uuid4()
    before = await financial_facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError)):
        await attempts(db_session, request, changed)
    assert await financial_facts(db_session) == before


async def test_outer_rollback_restores_attempts_and_all_checkout_facts(
    db_session: AsyncSession,
) -> None:
    request, ids = await prepared(db_session, ("cash", "card", "qr"))
    await confirm(db_session, await attempts(db_session, request, ids))
    before = await financial_facts(db_session)
    async with db_session.begin_nested() as caller:
        await complete(db_session, request)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await caller.rollback()
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("damage", ["missing", "amount", "attempt", "id"])
async def test_corrupt_component_rejects_entire_electronic_checkout(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, await attempts(db_session, request, ids))
    before = await financial_facts(db_session)
    insert = UnitCheckoutPaymentRepository.insert

    async def corrupt(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        if damage == "missing":
            return
        if damage == "amount":
            component.amount += Decimal("0.01")
        elif damage == "attempt":
            component.payment_attempt_id = uuid4()
        else:
            component.id = uuid4()
        await insert(repository, component)

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "insert", corrupt)
    with pytest.raises((DBAPIError, AurumError)):
        await complete(db_session, request)
    assert await financial_facts(db_session) == before


async def test_mixed_cart_has_no_internal_electronic_writer(db_session: AsyncSession) -> None:
    request, ids = await prepared(db_session, mixed=True)
    before = await financial_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await attempts(db_session, request, ids)
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("mode", ["readonly", "closed_shift"])
async def test_recovery_remains_read_only_after_shift_or_tenant_closes(
    db_session: AsyncSession, db_engine: AsyncEngine, mode: str
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, await attempts(db_session, request, ids))
    result = await complete(db_session, request)
    if mode == "closed_shift":
        await close_shift(db_session, result.shift_id)
    else:
        context = runtime(db_session)
        await db_session.commit()
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:id"),
                {"id": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await financial_facts(db_session)
    assert await complete(db_session, request, read=True) == result
    assert await financial_facts(db_session) == before


async def test_foreign_tenant_cannot_read_attempt_binding_or_completed_result(
    db_session: AsyncSession,
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, await attempts(db_session, request, ids))
    await complete(db_session, request)
    context = runtime(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    count = await db_session.scalar(text("SELECT count(*) FROM pos_unit_payment_attempt_binding"))
    assert count == 0
    with pytest.raises(AurumError):
        await complete(db_session, request, read=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert len(await journal(db_session)) == 1


@pytest.mark.parametrize("action", ["create", "reconciliation", "confirm", "void"])
async def test_public_terminal_write_routes_remain_blocked_for_unit_attempts(
    client: AsyncClient, db_session: AsyncSession, action: str
) -> None:
    request, ids = await prepared(db_session)
    rows = await attempts(db_session, request, ids)
    path = "/api/v1/pos/payment-attempts"
    payload: dict[str, object] = {}
    if action == "create":
        payload = {
            "operation_id": str(uuid4()),
            "sale_id": str(request.request.sale_id),
            "payment_method": "qr",
            "amount": str(request.request.expected_total_amount),
            "currency": "TJS",
        }
    else:
        path += f"/{rows[0].id}/{action}"
        if action == "confirm":
            payload = {"terminal_id": "TEST-TERMINAL", "external_reference": "test-public"}
        elif action == "void":
            payload = {"reason": "terminal_declined", "operator_note": None}
    before = await financial_facts(db_session)
    async with cart_actor(db_session):
        response = await client.post(path, json=payload)
    assert response.status_code == 422, response.text
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pos_unit_payment_attempt_binding SET amount=amount WHERE id=:id",
        "DELETE FROM pos_unit_payment_attempt_binding WHERE id=:id",
    ],
)
async def test_attempt_bindings_cannot_be_changed_or_deleted(
    db_session: AsyncSession, statement: str
) -> None:
    request, ids = await prepared(db_session)
    await attempts(db_session, request, ids)
    before = await financial_facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": ids["card"]})
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await financial_facts(db_session) == before


async def test_test_sale_cannot_bind_electronic_plan(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    await db_session.execute(
        text("UPDATE sale SET is_test=true WHERE id=:sale"), {"sale": request.request.sale_id}
    )
    current = await quote(db_session, request.request.sale_id)
    request = request.model_copy(
        update={
            "request": request.request.model_copy(
                update={"expected_revision_hash": current.revision_hash}
            )
        }
    )
    await save(db_session, request)
    before = await financial_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await binding(db_session, request, "bind")
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("channel", ["prepare", "complete", "read"])
async def test_current_branch_scope_is_required_even_for_recovery(
    db_session: AsyncSession, channel: str
) -> None:
    request, ids = await prepared(db_session)
    if channel != "prepare":
        await confirm(db_session, await attempts(db_session, request, ids))
    if channel == "read":
        await complete(db_session, request)
    context = runtime(db_session)
    service = UnitElectronicCheckoutService(UnitElectronicCheckoutRepository(db_session))
    before = await financial_facts(db_session)
    with pytest.raises(AurumError):
        if channel == "prepare":
            await service.prepare_attempts(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={uuid4()},
                sale_id=request.request.sale_id,
                operation_id=request.operation_id,
                attempt_ids=ids,
            )
        else:
            method = service.read if channel == "read" else service.complete
            await method(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={uuid4()},
                sale_id=request.request.sale_id,
                operation_id=request.operation_id,
            )
    assert await financial_facts(db_session) == before


async def test_cancelled_plan_cannot_prepare_attempts(db_session: AsyncSession) -> None:
    request, ids = await prepared(db_session)
    await binding(db_session, request, "cancel")
    before = await financial_facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError)):
        await attempts(db_session, request, ids)
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("damage", ["amount", "sale"])
async def test_binding_source_is_checked_before_global_foreign_keys(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    request, ids = await prepared(db_session)
    before = await financial_facts(db_session)
    insert = UnitElectronicCheckoutRepository.insert_binding

    async def corrupt(
        repository: UnitElectronicCheckoutRepository, row: UnitPaymentAttemptBinding
    ) -> None:
        if damage == "amount":
            row.amount += Decimal("0.01")
        else:
            row.sale_id = uuid4()
        await insert(repository, row)

    monkeypatch.setattr(UnitElectronicCheckoutRepository, "insert_binding", corrupt)
    with pytest.raises(DBAPIError) as rejected:
        await attempts(db_session, request, ids)
    assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("read", [False, True])
async def test_replay_rejects_missing_journal_without_any_writes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, read: bool
) -> None:
    request, ids = await prepared(db_session)
    await confirm(db_session, await attempts(db_session, request, ids))
    await complete(db_session, request)
    before = await financial_facts(db_session)

    async def empty(
        repository: UnitCheckoutPaymentRepository,
        tenant_id: UUID,
        checkout_id: UUID,
        *,
        mixed: bool,
    ) -> tuple[UnitCheckoutPaymentComponent, ...]:
        assert tenant_id == runtime(db_session).stock.tenant_id
        assert checkout_id == request.operation_id and mixed is False
        return ()

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "for_checkout", empty)
    with pytest.raises(AurumError):
        await complete(db_session, request, read=read)
    assert await financial_facts(db_session) == before


async def test_attempt_id_cannot_collide_with_checkout_operation(db_session: AsyncSession) -> None:
    request, _ = await prepared(db_session)
    before = await financial_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await attempts(db_session, request, {"card": request.operation_id})
    assert await financial_facts(db_session) == before


async def test_attempt_id_cannot_reuse_an_existing_pos_operation(db_session: AsyncSession) -> None:
    request, _ = await prepared(db_session)
    context = runtime(db_session)
    repository = POSRepository(db_session)
    original = await repository.get_sale(request.request.sale_id)
    assert original is not None
    occupied_id = uuid4()
    await POSService(repository).create_sale_command(
        tenant_id=context.stock.tenant_id,
        register_id=original.register_id,
        cashier_user_id=context.actor_id,
        operation_id=occupied_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    await db_session.flush()
    assert await repository.get_pos_operation_kinds(
        tenant_id=context.stock.tenant_id, operation_id=occupied_id
    )
    before = await financial_facts(db_session)
    with pytest.raises(ConflictError):
        await attempts(db_session, request, {"card": occupied_id})
    assert await financial_facts(db_session) == before


async def test_confirmation_uses_bound_plan_while_physical_draft_total_remains_zero(
    db_session: AsyncSession,
) -> None:
    request, ids = await prepared(db_session)
    total_query = text("SELECT total_amount FROM sale WHERE id=:sale")
    values = {"sale": request.request.sale_id}
    assert await db_session.scalar(total_query, values) == Decimal("0.00")
    rows = await attempts(db_session, request, ids)
    assert await db_session.scalar(total_query, values) == Decimal("0.00")
    await confirm(db_session, rows)
    assert await db_session.scalar(total_query, values) == Decimal("0.00")
    statuses = await db_session.execute(
        text("SELECT status FROM pos_payment_attempt WHERE sale_id=:sale"), values
    )
    assert statuses.scalars().all() == ["confirmed"]
    result = await complete(db_session, request)
    assert await db_session.scalar(total_query, values) == result.total_amount
    assert result.total_amount == request.request.expected_total_amount > Decimal("0.00")
