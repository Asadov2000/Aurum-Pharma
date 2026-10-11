"""The public whole-sale Rx path covers physical Rx beside legacy OTC rows."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.inventory.repository import InventoryRepository
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from app.domains.pos.unit_prescription_service import UnitPrescriptionService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import add_legacy
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def mixed_draft(
    session: AsyncSession, *, physical: bool = True
) -> tuple[Sale, SaleItem, UUID]:
    sale = await seed_cart_draft(session)
    context = runtime(session)
    otc = await CatalogService(CatalogRepository(session)).create_item(
        tenant_id=sale.tenant_id,
        fields={"brand_name": f"Synthetic mixed OTC {uuid4().hex}", "dispensing_type": "otc"},
        created_by=context.actor_id,
    )
    batch = await InventoryRepository(session).create_batch(
        tenant_id=sale.tenant_id,
        branch_id=sale.branch_id,
        catalog_id=otc.id,
        expires_at=date.today() + timedelta(days=180),
        purchase_price=Decimal("1.00"),
        sale_price=Decimal("2.00"),
        qty_initial=Decimal("1.000"),
        qty_remaining=Decimal("0.000"),
    )
    legacy = await POSRepository(session).insert_item(
        tenant_id=sale.tenant_id,
        sale_id=sale.id,
        catalog_id=otc.id,
        batch_id=batch.id,
        qty=Decimal("1.000"),
        unit_price=Decimal("2.00"),
        total_price=Decimal("2.00"),
        discount_amount=Decimal("0.00"),
        position=1,
        currency="TJS",
    )
    if physical:
        item_id = (await put_physical(session, sale.id)).id
    else:
        item_id = await put_cart_item(session, sale.id)
    await session.execute(
        text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    return sale, legacy, item_id


async def test_global_prescription_covers_physical_rx_with_only_legacy_otc(
    db_session: AsyncSession,
) -> None:
    sale, legacy, _ = await mixed_draft(db_session)
    context = runtime(db_session)
    row = await POSService(POSRepository(db_session)).add_prescription(
        sale_id=sale.id,
        fields={"prescription_number": " SYNTHETIC-MIXED-GLOBAL "},
        actor_id=context.actor_id,
        allowed_branch_ids={sale.branch_id},
    )
    assert row.sale_id == sale.id and row.tenant_id == sale.tenant_id
    assert row.sale_item_id is None and row.unit_cart_item_id is None
    assert row.created_by == context.actor_id
    assert row.prescription_number == "SYNTHETIC-MIXED-GLOBAL"
    assert legacy.catalog_id != context.stock.catalog_id
    await UnitPrescriptionService(UnitPrescriptionRepository(db_session)).require_complete(
        tenant_id=sale.tenant_id,
        sale_id=sale.id,
        actor_id=context.actor_id,
        allowed_branch_ids={sale.branch_id},
    )
    for table in ("sale_payment", "pos_unit_cash_intent", "pos_mixed_checkout"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"), {"sale": sale.id}
            )
            == 0
        )


@pytest.mark.parametrize("reference", ["legacy_otc", "foreign_legacy"])
async def test_physical_rx_does_not_allow_an_unrelated_explicit_legacy_reference(
    db_session: AsyncSession, reference: str
) -> None:
    sale, legacy, _ = await mixed_draft(db_session)
    context = runtime(db_session)
    pos = POSService(POSRepository(db_session))
    if reference == "foreign_legacy":
        other_sale = await pos.create_sale(
            tenant_id=sale.tenant_id,
            register_id=sale.register_id,
            cashier_user_id=context.actor_id,
            allowed_branch_ids={sale.branch_id},
        )
        legacy = await add_legacy(db_session, other_sale)
    error = NotFoundError if reference == "foreign_legacy" else BusinessRuleError
    with pytest.raises(error):
        await pos.add_prescription(
            sale_id=sale.id,
            fields={"sale_item_id": legacy.id, "prescription_number": "SYNTHETIC-UNRELATED"},
            actor_id=context.actor_id,
            allowed_branch_ids={sale.branch_id},
        )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM prescription_log WHERE sale_id=:sale"), {"sale": sale.id}
        )
        == 0
    )


@pytest.mark.parametrize("state", ["otc", "inactive", "deleted", "prepared_only"])
async def test_global_prescription_requires_an_active_physical_rx_position(
    db_session: AsyncSession, state: str
) -> None:
    sale, _, _ = await mixed_draft(db_session, physical=state != "prepared_only")
    context = runtime(db_session)
    changes = {
        "otc": "dispensing_type='otc'",
        "inactive": "is_active=false",
        "deleted": "deleted_at=now()",
    }
    if state in changes:
        await db_session.execute(
            text(f"UPDATE tenant_catalog SET {changes[state]} WHERE id=:id"),
            {"id": context.stock.catalog_id},
        )
    with pytest.raises(BusinessRuleError, match="not applicable"):
        await POSService(POSRepository(db_session)).add_prescription(
            sale_id=sale.id,
            fields={"prescription_number": "SYNTHETIC-NOT-APPLICABLE"},
            actor_id=context.actor_id,
            allowed_branch_ids={sale.branch_id},
        )


@pytest.mark.parametrize("scope_error", ["cashier", "branch"])
async def test_physical_global_prescription_preserves_owner_and_branch_checks(
    db_session: AsyncSession, scope_error: str
) -> None:
    sale, _, _ = await mixed_draft(db_session)
    context = runtime(db_session)
    with pytest.raises(PermissionDeniedError):
        await POSService(POSRepository(db_session)).add_prescription(
            sale_id=sale.id,
            fields={"prescription_number": "SYNTHETIC-DENIED"},
            actor_id=uuid4() if scope_error == "cashier" else context.actor_id,
            allowed_branch_ids={uuid4()} if scope_error == "branch" else {sale.branch_id},
        )


async def test_physical_global_prescription_cannot_read_a_foreign_tenant_sale(
    db_session: AsyncSession,
) -> None:
    sale, _, _ = await mixed_draft(db_session)
    context = runtime(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    try:
        with pytest.raises(NotFoundError, match="Sale not found"):
            await POSService(POSRepository(db_session)).add_prescription(
                sale_id=sale.id,
                fields={"prescription_number": "SYNTHETIC-FOREIGN-TENANT"},
                actor_id=context.actor_id,
                allowed_branch_ids={sale.branch_id},
            )
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM prescription_log WHERE sale_id=:sale"), {"sale": sale.id}
        )
        == 0
    )
