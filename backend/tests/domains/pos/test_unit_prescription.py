"""Unit Rx evidence stays scoped and cannot silently disappear with cart edits."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.repository import POSRepository
from app.domains.pos.schemas import PrescriptionLogCreate
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from app.domains.pos.unit_prescription_service import UnitPrescriptionService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_return import runtime
from tests.domains.pos.unit_shift_history_helpers import close_historical_unit_shift

db_session = receiving_session_fixture


def service(session: AsyncSession) -> UnitPrescriptionService:
    return UnitPrescriptionService(UnitPrescriptionRepository(session))


async def rx_cart(session: AsyncSession) -> tuple[UUID, UUID]:
    sale = await seed_cart_draft(session)
    await session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": runtime(session).stock.catalog_id},
    )
    return sale.id, await put_cart_item(session, sale.id)


async def attach(session: AsyncSession, sale_id: UUID, item_id: UUID) -> UUID:
    context = runtime(session)
    result = await service(session).add(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        unit_cart_item_id=item_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        details=PrescriptionLogCreate(prescription_number="SYNTHETIC-RX"),
    )
    assert set(result.model_dump()) == {"id", "sale_id", "unit_cart_item_id", "created_at"}
    return result.id


async def complete(session: AsyncSession, sale_id: UUID) -> None:
    context = runtime(session)
    await service(session).require_complete(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def test_missing_and_each_unit_position_require_evidence(db_session: AsyncSession) -> None:
    sale_id, first_id = await rx_cart(db_session)
    with pytest.raises(BusinessRuleError, match="all Rx unit positions"):
        await complete(db_session, sale_id)
    await attach(db_session, sale_id, first_id)
    await complete(db_session, sale_id)
    context = runtime(db_session)
    second = await UnitCartService(UnitCartRepository(db_session)).upsert_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        item_id=uuid4(),
        catalog_id=context.stock.catalog_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        position=2,
        sale_format="pack",
        quantity=1,
    )
    second_id = second.id
    with pytest.raises(BusinessRuleError, match="all Rx unit positions"):
        await complete(db_session, sale_id)
    await attach(db_session, sale_id, second_id)
    await complete(db_session, sale_id)
    for table in ("sale_item", "sale_payment", "pos_payment_attempt", "pos_unit_cash_intent"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"), {"sale": sale_id}
            )
            == 0
        )
    assert (
        await db_session.scalar(
            text("SELECT total_amount FROM sale WHERE id=:sale"), {"sale": sale_id}
        )
        == 0
    )


@pytest.mark.parametrize("details", ["SYNTHETIC-WHOLE-SALE", " \t\n"])
async def test_only_meaningful_whole_sale_evidence_covers_units(
    db_session: AsyncSession,
    details: str,
) -> None:
    sale_id, _ = await rx_cart(db_session)
    context = runtime(db_session)
    await db_session.execute(
        text(
            "INSERT INTO prescription_log (tenant_id,sale_id,prescription_number,created_by) "
            "VALUES (:tenant,:sale,:details,:actor)"
        ),
        {
            "tenant": context.stock.tenant_id,
            "sale": sale_id,
            "details": details,
            "actor": context.actor_id,
        },
    )
    if details.strip():
        await complete(db_session, sale_id)
    else:
        with pytest.raises(BusinessRuleError):
            await complete(db_session, sale_id)


async def test_explicit_unlink_required_and_cleanup_after_policy_change(
    db_session: AsyncSession,
) -> None:
    sale_id, item_id = await rx_cart(db_session)
    prescription_id = await attach(db_session, sale_id, item_id)
    context = runtime(db_session)
    cart = UnitCartService(UnitCartRepository(db_session))
    with pytest.raises(BusinessRuleError, match="Remove the linked prescription"):
        await cart.remove_item(
            tenant_id=context.stock.tenant_id,
            sale_id=sale_id,
            item_id=item_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
        )
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='otc' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    await service(db_session).remove(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        prescription_id=prescription_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    await cart.remove_item(
        tenant_id=context.stock.tenant_id,
        sale_id=sale_id,
        item_id=item_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def test_legacy_item_evidence_does_not_cover_units(db_session: AsyncSession) -> None:
    sale = await seed_cart_draft(db_session)
    legacy = await add_legacy(db_session, sale)
    context = runtime(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    await put_cart_item(db_session, sale.id)
    await db_session.execute(
        text(
            "INSERT INTO prescription_log (tenant_id,sale_id,sale_item_id,notes) "
            "VALUES (:tenant,:sale,:item,'SYNTHETIC')"
        ),
        {"tenant": context.stock.tenant_id, "sale": sale.id, "item": legacy.id},
    )
    with pytest.raises(BusinessRuleError):
        await complete(db_session, sale.id)
    with pytest.raises(DBAPIError, match="ck_prescription_item_kind"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO prescription_log "
                    "(tenant_id,sale_id,sale_item_id,unit_cart_item_id,notes) "
                    "SELECT :tenant,:sale,:legacy,id,'SYNTHETIC' "
                    "FROM pos_unit_cart_item WHERE sale_id=:sale"
                ),
                {"tenant": context.stock.tenant_id, "sale": sale.id, "legacy": legacy.id},
            )


@pytest.mark.parametrize("field", ["unit_cart_item_id", "sale_id", "created_by"])
async def test_raw_sql_cannot_unlink_or_reassign_evidence(
    db_session: AsyncSession,
    field: str,
) -> None:
    sale_id, item_id = await rx_cart(db_session)
    prescription_id = await attach(db_session, sale_id, item_id)
    with pytest.raises(DBAPIError, match="unit_prescription_identity_immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(f"UPDATE prescription_log SET {field}=NULL WHERE id=:id"),
                {"id": prescription_id},
            )
    await complete(db_session, sale_id)


async def test_other_sale_cart_reference_denied_by_service_and_database(
    db_session: AsyncSession,
) -> None:
    sale_id, item_id = await rx_cart(db_session)
    context = runtime(db_session)
    register_id = await db_session.scalar(
        text("SELECT register_id FROM sale WHERE id=:id"), {"id": sale_id}
    )
    second = await POSService(POSRepository(db_session)).create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    with pytest.raises(NotFoundError):
        await attach(db_session, second.id, item_id)
    with pytest.raises(DBAPIError, match="unit_prescription_cart_item_invalid"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO prescription_log (tenant_id,sale_id,unit_cart_item_id,notes) "
                    "VALUES (:tenant,:sale,:item,'SYNTHETIC')"
                ),
                {"tenant": context.stock.tenant_id, "sale": second.id, "item": item_id},
            )


@pytest.mark.parametrize("scope", ["tenant", "actor", "branch"])
async def test_forged_scope_cannot_attach(db_session: AsyncSession, scope: str) -> None:
    sale_id, item_id = await rx_cart(db_session)
    context = runtime(db_session)
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await service(db_session).add(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            sale_id=sale_id,
            unit_cart_item_id=item_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            details=PrescriptionLogCreate(notes="SYNTHETIC"),
        )


async def test_prescription_permission_is_separate_from_selling(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    async with maintenance_engine.connect() as connection:
        permissions = tuple(
            await connection.scalars(
                text("SELECT code FROM permission WHERE code<>'pos.handle_prescription'")
            )
        )
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=permissions
    )
    db_session.info["receiving_runtime"] = replace(context, actor_id=actor)
    await set_receiving_context(db_session, context.stock, actor)
    sale_id, item_id = await rx_cart(db_session)
    with pytest.raises(PermissionDeniedError, match="handling permission"):
        await attach(db_session, sale_id, item_id)
    with pytest.raises(DBAPIError, match="unit_prescription_actor_denied"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO prescription_log (tenant_id,sale_id,unit_cart_item_id,notes) "
                    "VALUES (:tenant,:sale,:item,'SYNTHETIC')"
                ),
                {"tenant": context.stock.tenant_id, "sale": sale_id, "item": item_id},
            )


async def test_revalidation_rejects_forged_empty_details_and_legacy_reference(
    db_session: AsyncSession,
) -> None:
    sale_id, item_id = await rx_cart(db_session)
    context = runtime(db_session)
    for details, exception in (
        (
            PrescriptionLogCreate(notes="SYNTHETIC").model_copy(update={"notes": "  "}),
            ValidationError,
        ),
        (PrescriptionLogCreate(notes="SYNTHETIC", sale_item_id=uuid4()), BusinessRuleError),
    ):
        with pytest.raises(exception):
            await service(db_session).add(
                tenant_id=context.stock.tenant_id,
                sale_id=sale_id,
                unit_cart_item_id=item_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                details=details,
            )


async def test_recipe_details_remain_masked_in_audit(db_session: AsyncSession) -> None:
    sale_id, item_id = await rx_cart(db_session)
    prescription_id = await attach(db_session, sale_id, item_id)
    assert (
        await db_session.scalar(
            text(
                "SELECT new_values->>'prescription_number' FROM audit_log "
                "WHERE table_name='prescription_log' AND record_id=:id AND action='INSERT'"
            ),
            {"id": prescription_id},
        )
        == "***"
    )


async def test_current_special_policy_and_closed_shift_block_unit_prescriptions(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    sale_id, item_id = await rx_cart(db_session)
    await attach(db_session, sale_id, item_id)
    context = runtime(db_session)
    await db_session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='special' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    with pytest.raises(BusinessRuleError, match="Special-dispensing"):
        await complete(db_session, sale_id)
    shift_id = await db_session.scalar(
        text("SELECT shift_id FROM sale WHERE id=:sale"), {"sale": sale_id}
    )
    assert isinstance(shift_id, UUID)
    await close_historical_unit_shift(db_session, maintenance_engine, shift_id)
    with pytest.raises(BusinessRuleError):
        await attach(db_session, sale_id, item_id)
