"""Idempotency and recovery contract for mutable POS draft commands."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import POSCommand, Sale, SaleItem
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import SaleCreate, SaleItemAdd, SaleItemDelete, SaleItemPatch
from app.domains.pos.service import POSService


async def _open_shift(service: POSService, scaffold) -> None:
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )


def test_command_payloads_require_uuid4_operation_id() -> None:
    register_id = uuid4()
    catalog_id = uuid4()

    for schema, payload in (
        (SaleCreate, {"register_id": register_id}),
        (SaleItemAdd, {"catalog_id": catalog_id, "qty": "1"}),
        (SaleItemPatch, {"qty": "1"}),
        (SaleItemDelete, {}),
    ):
        with pytest.raises(ValidationError):
            schema.model_validate(payload)
        with pytest.raises(ValidationError):
            schema.model_validate({**payload, "operation_id": uuid1()})


async def test_create_command_replays_and_rejects_hash_or_type_conflicts(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()

    first = await service.create_sale_command(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
    )
    replay = await service.create_sale_command(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
    )

    assert replay == first
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(POSCommand)
            .where(POSCommand.operation_id == operation_id)
        )
        == 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(Sale).where(Sale.id == first.id))
        == 1
    )

    with pytest.raises(ConflictError, match="another POS command"):
        await service.create_sale_command(
            tenant_id=scaffold["tenant"].id,
            register_id=uuid4(),
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
        )
    with pytest.raises(ConflictError, match="another POS command"):
        await service.add_item_command(
            tenant_id=scaffold["tenant"].id,
            sale_id=first.id,
            catalog_id=scaffold["item"].id,
            qty=Decimal("1"),
            expired_sale_confirmed=False,
            actor_id=scaffold["cashier"].id,
            operation_id=operation_id,
        )
    with pytest.raises(ConflictError, match="another POS operation"):
        await service.create_payment_attempt(
            tenant_id=scaffold["tenant"].id,
            sale_id=first.id,
            actor_id=scaffold["cashier"].id,
            operation_id=operation_id,
            payment_method="card",
            amount=Decimal("1"),
            currency="TJS",
        )


async def test_item_commands_replay_canonical_results_and_delete_once(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"), batch_qty=Decimal("20"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )

    add_operation = uuid4()
    added = await service.add_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("2"),
        expired_sale_confirmed=False,
        actor_id=scaffold["cashier"].id,
        operation_id=add_operation,
    )
    added_replay = await service.add_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        catalog_id=scaffold["item"].id,
        qty=Decimal("2.000"),
        expired_sale_confirmed=True,
        actor_id=scaffold["cashier"].id,
        operation_id=add_operation,
    )
    assert added_replay == added
    assert len(added.items) == 1

    with pytest.raises(ConflictError, match="another POS command"):
        await service.add_item_command(
            tenant_id=scaffold["tenant"].id,
            sale_id=sale.id,
            catalog_id=scaffold["item"].id,
            qty=Decimal("3"),
            expired_sale_confirmed=False,
            actor_id=scaffold["cashier"].id,
            operation_id=add_operation,
        )

    item_id = added.items[0].id
    update_operation = uuid4()
    updated = await service.update_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        item_id=item_id,
        qty=Decimal("1"),
        actor_id=scaffold["cashier"].id,
        operation_id=update_operation,
    )
    updated_replay = await service.update_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        item_id=item_id,
        qty=Decimal("1.000"),
        actor_id=scaffold["cashier"].id,
        operation_id=update_operation,
    )
    assert updated_replay == updated

    delete_operation = uuid4()
    deleted = await service.delete_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        item_id=item_id,
        actor_id=scaffold["cashier"].id,
        operation_id=delete_operation,
    )
    deleted_replay = await service.delete_item_command(
        tenant_id=scaffold["tenant"].id,
        sale_id=sale.id,
        item_id=item_id,
        actor_id=scaffold["cashier"].id,
        operation_id=delete_operation,
    )
    assert deleted_replay == deleted
    assert await db_session.get(SaleItem, item_id) is None


async def test_command_recovery_is_scoped_to_tenant_and_actor(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    first = await pos_scaffold(sale_price=Decimal("10"))
    second = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, first)
    await _open_shift(service, second)
    operation_id = uuid4()

    first_sale = await service.create_sale_command(
        tenant_id=first["tenant"].id,
        register_id=first["register"].id,
        cashier_user_id=first["cashier"].id,
        operation_id=operation_id,
    )
    recovered = await service.get_pos_command_result(
        tenant_id=first["tenant"].id,
        actor_user_id=first["cashier"].id,
        operation_id=operation_id,
    )
    assert recovered.sale_id == first_sale.id

    with pytest.raises(NotFoundError, match="not found"):
        await service.get_pos_command_result(
            tenant_id=first["tenant"].id,
            actor_user_id=second["cashier"].id,
            operation_id=operation_id,
        )
    with pytest.raises(NotFoundError, match="not found"):
        await service.get_pos_command_result(
            tenant_id=second["tenant"].id,
            actor_user_id=first["cashier"].id,
            operation_id=operation_id,
        )
    with pytest.raises(ConflictError, match="another POS command"):
        await service.create_sale_command(
            tenant_id=first["tenant"].id,
            register_id=first["register"].id,
            cashier_user_id=second["cashier"].id,
            operation_id=operation_id,
        )

    second_sale = await service.create_sale_command(
        tenant_id=second["tenant"].id,
        register_id=second["register"].id,
        cashier_user_id=second["cashier"].id,
        operation_id=operation_id,
    )
    assert second_sale.id != first_sale.id


async def test_command_recovery_rechecks_current_branch_scope(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    operation_id = uuid4()

    await service.create_sale_command(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=operation_id,
    )
    revoked_scope = {uuid4()}

    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.create_sale_command(
            tenant_id=scaffold["tenant"].id,
            register_id=scaffold["register"].id,
            cashier_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            allowed_branch_ids=revoked_scope,
        )
    with pytest.raises(PermissionDeniedError, match="Branch access denied"):
        await service.get_pos_command_result(
            tenant_id=scaffold["tenant"].id,
            actor_user_id=scaffold["cashier"].id,
            operation_id=operation_id,
            allowed_branch_ids=revoked_scope,
        )


async def test_command_rejects_operation_claimed_by_existing_pos_namespaces(
    db_session: AsyncSession,
    pos_scaffold,
) -> None:
    scaffold = await pos_scaffold(sale_price=Decimal("10"))
    service = POSService(POSRepository(db_session))
    await _open_shift(service, scaffold)
    sale_operation_id = uuid4()
    await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=sale_operation_id,
        operation_hash="a" * 64,
    )
    draft = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    payment_operation_id = uuid4()
    await service.repo.insert_payment(
        tenant_id=scaffold["tenant"].id,
        sale_id=draft.id,
        payment_method="cash",
        amount=Decimal("1"),
        operation_id=payment_operation_id,
        operation_hash="b" * 64,
    )
    payment_attempt_operation_id = uuid4()
    await service.repo.insert_payment_attempt(
        tenant_id=scaffold["tenant"].id,
        sale_id=draft.id,
        cashier_user_id=scaffold["cashier"].id,
        operation_id=payment_attempt_operation_id,
        operation_hash="c" * 64,
        payment_method="card",
        amount=Decimal("1"),
        currency="TJS",
        status="pending",
    )
    refund_attempt_operation_id = uuid4()
    await service.repo.insert_refund_attempt(
        tenant_id=scaffold["tenant"].id,
        parent_sale_id=draft.id,
        register_id=scaffold["register"].id,
        requested_by_user_id=scaffold["cashier"].id,
        operation_id=refund_attempt_operation_id,
        operation_hash="d" * 64,
        items_json=[{"sale_item_id": str(uuid4()), "qty": "1"}],
        external_allocations_json=[{"payment_method": "card", "amount": "1.00"}],
        total_amount=Decimal("1"),
        external_amount=Decimal("1"),
        currency="TJS",
        status="pending",
    )

    for operation_id in (
        sale_operation_id,
        payment_operation_id,
        payment_attempt_operation_id,
        refund_attempt_operation_id,
    ):
        with pytest.raises(ConflictError, match="another POS operation"):
            await service.create_sale_command(
                tenant_id=scaffold["tenant"].id,
                register_id=scaffold["register"].id,
                cashier_user_id=scaffold["cashier"].id,
                operation_id=operation_id,
            )
