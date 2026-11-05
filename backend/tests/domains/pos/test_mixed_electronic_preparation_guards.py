"""Exercise mixed preparation guards without opening a database connection."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2
from app.domains.pos.mixed_checkout_preparation import MixedPhysicalCheckoutPreparationService
from app.domains.pos.mixed_electronic_checkout_preparation import prepare_electronic_mixed_cart
from app.domains.pos.mixed_electronic_checkout_source import require_mixed_electronic_source
from app.domains.pos.models import Sale, SaleItem, Shift
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_models import UnitCartItem
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_checkout_preparation import UnitCheckoutPreparationService
from app.domains.pos.unit_checkout_repository import UnitCheckoutRepository
from app.domains.pos.unit_payment_binding_models import UnitPaymentBinding
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent
from app.domains.pos.unit_payment_plan import (
    UnitPaymentPart,
    UnitPaymentPlanRequest,
    ValidatedUnitPaymentPlan,
    payment_plan_hash,
)
from app.domains.pos.unit_prescription_repository import UnitPrescriptionRepository
from tests.domains.pos.test_mixed_electronic_preparation_contract import quote


@dataclass
class PreparationContext:
    session: AsyncSession
    sale: Sale
    saved: SavedUnitPaymentIntent
    legacy_read: AsyncMock
    catalogs: AsyncMock
    references: AsyncMock
    stock: AsyncMock
    register: AsyncMock
    binding: AsyncMock
    actor: AsyncMock
    payment_configuration: AsyncMock


def context(monkeypatch: pytest.MonkeyPatch) -> PreparationContext:
    frozen = quote()
    request = UnitPaymentPlanRequest(
        sale_id=frozen.sale_id,
        expected_revision_hash=frozen.revision_hash,
        expected_total_amount=frozen.total_amount,
        payments=(
            UnitPaymentPart(method="cash", amount=Decimal("3.52")),
            UnitPaymentPart(method="card", amount=Decimal("10.00")),
            UnitPaymentPart(method="qr", amount=Decimal("10.00")),
        ),
        cash_tendered=Decimal("5.00"),
    )
    saved = SavedUnitPaymentIntent(
        operation_id=uuid4(),
        saved_at=datetime.now(UTC),
        plan=ValidatedUnitPaymentPlan(
            request=request,
            quote=frozen,
            change_amount=Decimal("1.48"),
            plan_hash=payment_plan_hash(request, frozen),
        ),
    )
    sale = Sale(
        tenant_id=frozen.tenant_id,
        id=frozen.sale_id,
        branch_id=frozen.branch_id,
        register_id=frozen.register_id,
        shift_id=frozen.shift_id,
        cashier_user_id=frozen.cashier_id,
        status="draft",
        is_test=False,
        sale_type="sale",
        currency="TJS",
    )
    shift = Shift(
        status="open",
        branch_id=sale.branch_id,
        register_id=sale.register_id,
        opened_by_user_id=sale.cashier_user_id,
        currency="TJS",
    )
    physical = frozen.lines[1]
    batch = UnitStockBatchV2.model_validate(
        {
            "id": uuid4(),
            "tenant_id": sale.tenant_id,
            "branch_id": sale.branch_id,
            "catalog_id": physical.catalog_id,
            "packaging": physical.model_dump()["packaging"],
            "expires_at": "2027-01-01",
            "created_at": "2026-01-01T00:00:00Z",
            "closed_packs": 2,
            "opened_packs": [],
        }
    )
    session = AsyncSession()
    monkeypatch.setattr(session, "scalar", AsyncMock(side_effect=AssertionError("Unexpected SQL")))
    catalogs = AsyncMock(side_effect=[retail_catalog() for _ in frozen.lines])
    references = AsyncMock(return_value=())
    stock = AsyncMock(return_value=(batch,))
    register = AsyncMock(return_value=True)
    binding = AsyncMock(return_value=(UnitPaymentBinding(sale_id=sale.id), None))
    actor = AsyncMock()
    payment_configuration = AsyncMock()
    legacy, units = source_rows()
    legacy_read = AsyncMock(return_value=legacy)
    replacements = (
        (UnitCheckoutRepository, "lock_checkout_catalog", catalogs),
        (UnitPrescriptionRepository, "references", references),
        (
            MixedPhysicalCheckoutPreparationService,
            "_legacy_allocations",
            AsyncMock(return_value=()),
        ),
        (UnitCheckoutRepository, "lock_physical_batches", stock),
        (UnitCartRepository, "active_register", register),
        (UnitCartRepository, "lock_shift", AsyncMock(return_value=shift)),
        (UnitCartRepository, "list_legacy_items", legacy_read),
        (UnitCartRepository, "list_items", AsyncMock(return_value=units)),
        (UnitPaymentBindingRepository, "get_state", binding),
        (UnitCartRepository, "assert_actor", actor),
        (
            UnitCheckoutPreparationService,
            "_physical_stock_date",
            AsyncMock(return_value=date(2026, 10, 20)),
        ),
        (POSService, "_validate_pos_payment_configuration", payment_configuration),
    )
    for target, name, replacement in replacements:
        monkeypatch.setattr(target, name, replacement)
    return PreparationContext(
        session,
        sale,
        saved,
        legacy_read,
        catalogs,
        references,
        stock,
        register,
        binding,
        actor,
        payment_configuration,
    )


def retail_catalog(*, prescription: bool = False) -> TenantCatalog:
    return TenantCatalog(
        is_active=True,
        deleted_at=None,
        currency="TJS",
        dispensing_type="prescription" if prescription else "otc",
        base_price=Decimal("37.50"),
        unit_sale_preparation={
            "version": 1,
            "state": "prepared",
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10,
            "dispense_step": 1,
            "unit_price": "1.25",
        },
    )


def source_rows() -> tuple[tuple[SaleItem, ...], tuple[UnitCartItem, ...]]:
    saved = quote()
    legacy = saved.lines[0]
    physical = saved.lines[1]
    return (
        (
            SaleItem(
                tenant_id=saved.tenant_id,
                sale_id=saved.sale_id,
                id=legacy.id,
                catalog_id=legacy.catalog_id,
                batch_id=legacy.model_dump()["batch_id"],
                qty=legacy.quantity,
                unit_price=legacy.original_retail_unit_price,
                discount_amount=legacy.discount_amount,
                total_price=legacy.net_amount,
                parent_sale_item_id=None,
                currency="TJS",
            ),
        ),
        (
            UnitCartItem(
                tenant_id=saved.tenant_id,
                sale_id=saved.sale_id,
                branch_id=saved.branch_id,
                id=physical.id,
                catalog_id=physical.catalog_id,
                physical_packaging=physical.model_dump()["packaging"],
                quantity=physical.quantity,
                discount_amount=physical.discount_amount,
                sale_format=physical.model_dump()["sale_format"],
            ),
        ),
    )


async def test_preparation_checks_all_methods_and_leaves_session_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = context(monkeypatch)
    try:
        result = await prepare_electronic_mixed_cart(
            UnitCartRepository(source.session), source.sale, source.saved
        )
        assert result.state == "checked_mixed_electronic_draft"
        assert len(result.physical_stock_plan.lines) == 1
        source.payment_configuration.assert_awaited_once_with(
            tenant_id=source.sale.tenant_id, requested_methods={"cash", "card", "qr"}
        )
        assert source.register.await_count == 2
        source.actor.assert_awaited_once_with(
            source.sale.tenant_id, source.sale.cashier_user_id, source.sale.branch_id
        )
        assert not source.session.new and not source.session.dirty and not source.session.deleted
    finally:
        await source.session.close()


@pytest.mark.parametrize(
    "failure",
    ["changed_quote", "disabled_method", "stock", "cancelled", "late_register", "late_actor"],
)
async def test_preparation_fails_closed_without_financial_write(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = context(monkeypatch)
    if failure == "changed_quote":
        source.legacy_read.return_value = ()
    elif failure == "disabled_method":
        source.payment_configuration.side_effect = BusinessRuleError("Payment method disabled")
    elif failure == "stock":
        source.stock.return_value = ()
    elif failure == "cancelled":
        source.binding.return_value = (None, None)
    elif failure == "late_register":
        source.register.side_effect = [True, False]
    else:
        source.actor.side_effect = PermissionDeniedError("Access changed while waiting")
    try:
        with pytest.raises((BusinessRuleError, ConflictError, PermissionDeniedError)):
            await prepare_electronic_mixed_cart(
                UnitCartRepository(source.session), source.sale, source.saved
            )
        assert not source.session.new and not source.session.dirty and not source.session.deleted
        if failure in {"changed_quote", "disabled_method"}:
            source.stock.assert_not_awaited()
        if failure == "changed_quote":
            source.payment_configuration.assert_not_awaited()
    finally:
        await source.session.close()


@pytest.mark.parametrize("covered", ["none", "legacy", "physical", "both", "global"])
async def test_prescription_coverage_is_required_for_both_stock_kinds(
    monkeypatch: pytest.MonkeyPatch, covered: str
) -> None:
    source = context(monkeypatch)
    source.catalogs.side_effect = None
    source.catalogs.return_value = retail_catalog(prescription=True)
    legacy_id, physical_id = (line.id for line in source.saved.plan.quote.lines)
    source.references.return_value = {
        "none": (),
        "legacy": ((legacy_id, None),),
        "physical": ((None, physical_id),),
        "both": ((legacy_id, None), (None, physical_id)),
        "global": ((None, None),),
    }[covered]
    try:
        if covered in {"both", "global"}:
            await prepare_electronic_mixed_cart(
                UnitCartRepository(source.session), source.sale, source.saved
            )
        else:
            with pytest.raises(BusinessRuleError, match="all mixed Rx"):
                await prepare_electronic_mixed_cart(
                    UnitCartRepository(source.session), source.sale, source.saved
                )
            source.stock.assert_not_awaited()
    finally:
        await source.session.close()


@pytest.mark.parametrize(
    "stock_kind,field,value",
    [
        ("legacy", "id", uuid4()),
        ("legacy", "tenant_id", uuid4()),
        ("legacy", "sale_id", uuid4()),
        ("legacy", "catalog_id", uuid4()),
        ("legacy", "batch_id", uuid4()),
        ("legacy", "parent_sale_item_id", uuid4()),
        ("legacy", "currency", "USD"),
        ("legacy", "qty", Decimal("0.002")),
        ("legacy", "unit_price", Decimal("25.01")),
        ("legacy", "discount_amount", Decimal("0.01")),
        ("legacy", "total_price", Decimal("0.01")),
        ("physical", "id", uuid4()),
        ("physical", "tenant_id", uuid4()),
        ("physical", "sale_id", uuid4()),
        ("physical", "branch_id", uuid4()),
        ("physical", "catalog_id", uuid4()),
        ("physical", "sale_format", "unit"),
        ("physical", "quantity", 3),
        ("physical", "discount_amount", Decimal("1.51")),
        ("physical", "physical_packaging", None),
    ],
)
def test_bound_mixed_source_rejects_changed_row_facts(
    stock_kind: str, field: str, value: object
) -> None:
    saved = quote()
    legacy, units = source_rows()
    setattr(legacy[0] if stock_kind == "legacy" else units[0], field, value)
    catalogs = {line.catalog_id: retail_catalog() for line in saved.lines}
    with pytest.raises(ConflictError):
        require_mixed_electronic_source(saved, legacy, units, catalogs)


def test_bound_mixed_source_rejects_current_physical_price_drift() -> None:
    saved = quote()
    legacy, units = source_rows()
    catalogs = {line.catalog_id: retail_catalog() for line in saved.lines}
    physical_catalog = catalogs[saved.lines[1].catalog_id]
    assert physical_catalog.unit_sale_preparation is not None
    physical_catalog.unit_sale_preparation = {
        **physical_catalog.unit_sale_preparation,
        "unit_price": "1.26",
    }
    with pytest.raises(ConflictError, match="prices changed"):
        require_mixed_electronic_source(saved, legacy, units, catalogs)


@pytest.mark.parametrize(
    "field,value", [("parent_sale_id", uuid4()), ("is_test", True), ("status", "completed")]
)
async def test_non_original_or_non_draft_sale_is_rejected_before_reads(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    source = context(monkeypatch)
    setattr(source.sale, field, value)
    try:
        with pytest.raises(BusinessRuleError, match="real sale draft"):
            await prepare_electronic_mixed_cart(
                UnitCartRepository(source.session), source.sale, source.saved
            )
        source.catalogs.assert_not_awaited()
        source.stock.assert_not_awaited()
        source.legacy_read.assert_not_awaited()
    finally:
        await source.session.close()
