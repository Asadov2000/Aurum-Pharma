"""Dispensing preflight reads only scoped draft items and fails closed on catalog facts."""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.pos.models import Sale, SaleItem, Shift
from app.domains.pos.physical_cart_dispensing_service import PhysicalCartDispensingPreflightService
from app.domains.pos.physical_cart_schemas import PhysicalCartDispensingPreflightRead
from app.domains.pos.unit_cart_models import UnitCartItem
from app.domains.pos.unit_cart_repository import UnitCartRepository


@dataclass
class Preflight:
    tenant: UUID
    actor: UUID
    branch: UUID
    sale: Sale
    shift: Shift
    catalogs: tuple[TenantCatalog, TenantCatalog]
    repository: MagicMock

    async def read(self) -> PhysicalCartDispensingPreflightRead:
        return await PhysicalCartDispensingPreflightService(self.repository).read(
            tenant_id=self.tenant,
            actor_id=self.actor,
            sale_id=self.sale.id,
            allowed_branch_ids={self.branch},
        )


@pytest.fixture
def preflight() -> Preflight:
    tenant, actor, branch, register, shift_id, sale_id = (uuid4() for _ in range(6))
    sale = Sale(
        id=sale_id,
        tenant_id=tenant,
        branch_id=branch,
        cashier_user_id=actor,
        register_id=register,
        shift_id=shift_id,
        sale_type="sale",
        status="draft",
        currency="TJS",
    )
    shift = Shift(
        id=shift_id,
        tenant_id=tenant,
        branch_id=branch,
        register_id=register,
        opened_by_user_id=actor,
        status="open",
        currency="TJS",
    )
    catalogs = tuple(
        TenantCatalog(
            id=uuid4(), tenant_id=tenant, is_active=True, currency="TJS", dispensing_type="otc"
        )
        for _ in range(2)
    )
    repository = MagicMock(spec=UnitCartRepository)
    repository.session = MagicMock(spec=AsyncSession)
    repository.session.no_autoflush = nullcontext()
    repository.lock_sale.return_value = sale
    repository.lock_shift.return_value = shift
    repository.active_register.return_value = True
    repository.active_branch.return_value = True
    repository.has_payment_activity.return_value = False
    repository.list_items.return_value = (
        UnitCartItem(tenant_id=tenant, sale_id=sale_id, catalog_id=catalogs[0].id),
    )
    repository.list_legacy_items.return_value = (
        SaleItem(tenant_id=tenant, sale_id=sale_id, catalog_id=catalogs[1].id),
    )
    by_id = {catalog.id: catalog for catalog in catalogs}
    repository.lock_catalog.side_effect = lambda tenant_id, catalog_id: by_id[catalog_id]
    return Preflight(tenant, actor, branch, sale, shift, (catalogs[0], catalogs[1]), repository)


@pytest.mark.parametrize(
    ("physical", "legacy", "requires_prescription"),
    [("otc", "otc", False), ("prescription", "otc", True), ("otc", "prescription", True)],
)
async def test_preflight_includes_both_actual_item_kinds_without_financial_writes(
    preflight: Preflight, physical: str, legacy: str, requires_prescription: bool
) -> None:
    preflight.catalogs[0].dispensing_type = physical
    preflight.catalogs[1].dispensing_type = legacy
    result = await preflight.read()
    assert result.model_dump() == {
        "sale_id": preflight.sale.id,
        "requires_prescription": requires_prescription,
    }
    assert preflight.repository.lock_catalog.await_count == 2
    assert {call.args for call in preflight.repository.lock_catalog.await_args_list} == {
        (preflight.tenant, catalog.id) for catalog in preflight.catalogs
    }
    preflight.repository.session.flush.assert_not_called()
    preflight.repository.session.begin_nested.assert_not_called()
    preflight.repository.session.add.assert_not_called()


async def test_preflight_reads_each_actual_catalog_once(preflight: Preflight) -> None:
    preflight.repository.list_legacy_items.return_value = (
        SaleItem(
            tenant_id=preflight.tenant,
            sale_id=preflight.sale.id,
            catalog_id=preflight.catalogs[0].id,
        ),
    )
    await preflight.read()
    preflight.repository.lock_catalog.assert_awaited_once_with(
        preflight.tenant, preflight.catalogs[0].id
    )


@pytest.mark.parametrize("kind", ["physical", "legacy"])
async def test_preflight_rejects_invalid_catalog_even_after_an_rx_was_found(
    preflight: Preflight, kind: str
) -> None:
    preflight.catalogs[0].dispensing_type = "prescription"
    unavailable = preflight.catalogs[0 if kind == "physical" else 1]
    unavailable.dispensing_type = "unrecognized"
    with pytest.raises(BusinessRuleError, match="unavailable medicine"):
        await preflight.read()


@pytest.mark.parametrize("restriction", ["inactive", "deleted", "special", "currency", "missing"])
async def test_preflight_fails_closed_for_unavailable_actual_catalog(
    preflight: Preflight, restriction: str
) -> None:
    catalog = preflight.catalogs[0]
    if restriction == "inactive":
        catalog.is_active = False
    elif restriction == "deleted":
        catalog.deleted_at = datetime.now(UTC)
    elif restriction == "special":
        catalog.dispensing_type = "special"
    elif restriction == "currency":
        catalog.currency = "USD"
    else:
        preflight.repository.lock_catalog.side_effect = None
        preflight.repository.lock_catalog.return_value = None
    with pytest.raises((BusinessRuleError, NotFoundError)):
        await preflight.read()


async def test_preflight_rejects_empty_cart_and_does_not_query_catalog(
    preflight: Preflight,
) -> None:
    preflight.repository.list_items.return_value = ()
    preflight.repository.list_legacy_items.return_value = ()
    with pytest.raises(BusinessRuleError, match="nonempty"):
        await preflight.read()
    preflight.repository.lock_catalog.assert_not_awaited()


@pytest.mark.parametrize(
    "denial", ["cashier", "branch", "draft", "shift", "register", "payment", "inactive_branch"]
)
async def test_preflight_preserves_existing_cart_authority_and_state_guards(
    preflight: Preflight, denial: str
) -> None:
    if denial == "cashier":
        preflight.sale.cashier_user_id = uuid4()
    elif denial == "branch":
        preflight.sale.branch_id = uuid4()
    elif denial == "draft":
        preflight.sale.status = "completed"
    elif denial == "shift":
        preflight.shift.status = "closed"
    elif denial == "register":
        preflight.repository.active_register.return_value = False
    elif denial == "inactive_branch":
        preflight.repository.active_branch.return_value = False
    else:
        preflight.repository.has_payment_activity.return_value = True
    with pytest.raises((BusinessRuleError, ConflictError, PermissionDeniedError)):
        await preflight.read()
    preflight.repository.lock_catalog.assert_not_awaited()


async def test_preflight_rechecks_current_actor_and_branch_before_catalog(
    preflight: Preflight,
) -> None:
    preflight.repository.assert_actor.side_effect = [None, PermissionDeniedError("Access revoked")]
    with pytest.raises(PermissionDeniedError, match="revoked"):
        await preflight.read()
    preflight.repository.assert_actor.assert_any_await(preflight.tenant, preflight.actor)
    preflight.repository.assert_actor.assert_any_await(
        preflight.tenant, preflight.actor, preflight.branch
    )
    preflight.repository.lock_catalog.assert_not_awaited()
