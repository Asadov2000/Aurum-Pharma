"""Scoped read-only physical format, retail and stock facts; no checkout activation."""

from __future__ import annotations

from decimal import Decimal
from math import gcd
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import AurumError, BusinessRuleError, NotFoundError
from app.core.time import utc_now
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.unit_sales import UnitSalePreparation
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_repository import physical_stock_snapshots_v2
from app.domains.inventory.unit_dispensing_v2_retail import quote_retail_line_v2
from app.domains.inventory.unit_stock_v2 import (
    SaleFormatV2,
    UnitPackagingV2,
    stock_availability_v2,
)
from app.domains.pos.physical_cart_schemas import (
    PhysicalCartFormatOption,
    PhysicalCartOptionsRead,
    PhysicalCartStockRead,
    PhysicalOptionUnavailableReason,
)
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService

_FORMATS: tuple[SaleFormatV2, SaleFormatV2, SaleFormatV2] = ("pack", "blister", "unit")


def _unavailable(
    sale_format: SaleFormatV2, reason: PhysicalOptionUnavailableReason
) -> PhysicalCartFormatOption:
    return PhysicalCartFormatOption(
        sale_format=sale_format,
        configured=False,
        quantity_step=None,
        retail_unit_price=None,
        unavailable_reason=reason,
    )


def _format_option(
    catalog: TenantCatalog, packaging: UnitPackagingV2, sale_format: SaleFormatV2
) -> PhysicalCartFormatOption:
    quantity_step = 1
    if sale_format == "blister":
        if packaging.units_per_blister is None:
            return _unavailable(sale_format, "unknown_blister")
        quantity_step = packaging.dispense_step // gcd(
            packaging.dispense_step, packaging.units_per_blister
        )
    elif sale_format == "unit":
        quantity_step = packaging.dispense_step
    try:
        amounts = quote_retail_line_v2(
            UnitCartLineV2(
                id=catalog.id,
                catalog_id=catalog.id,
                packaging=packaging,
                sale_format=sale_format,
                quantity=quantity_step,
            ),
            catalog,
            Decimal("0.00"),
        )
    except (BusinessRuleError, ValueError):
        return PhysicalCartFormatOption(
            sale_format=sale_format,
            configured=False,
            quantity_step=quantity_step,
            retail_unit_price=None,
            unavailable_reason="price_unavailable",
        )
    return PhysicalCartFormatOption(
        sale_format=sale_format,
        configured=True,
        quantity_step=quantity_step,
        retail_unit_price=amounts.original_retail_unit_price,
        unavailable_reason=None,
    )


class PhysicalCartOptionsService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        catalog_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> PhysicalCartOptionsRead:
        async with self.repository.session.begin_nested():
            sale = await UnitCartService(self.repository).lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            catalog = await self.repository.lock_catalog(tenant_id, catalog_id)
            if catalog is None:
                raise NotFoundError("Catalog item not found")
            settings = await FoundationRepository(self.repository.session).get_settings(tenant_id)
            timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
            try:
                today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise AurumError("Tenant report timezone is invalid") from error

            packaging: UnitPackagingV2 | None = None
            unavailable_reason: PhysicalOptionUnavailableReason | None = None
            if (
                not catalog.is_active
                or catalog.deleted_at is not None
                or catalog.currency != "TJS"
                or catalog.dispensing_type == "special"
            ):
                unavailable_reason = "catalog_unavailable"
            else:
                try:
                    preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
                    packaging = UnitPackagingV2(
                        unit=preparation.unit,
                        units_per_pack=preparation.units_per_pack,
                        units_per_blister=preparation.units_per_blister,
                        dispense_step=preparation.dispense_step,
                    )
                except ValueError:
                    unavailable_reason = "not_prepared"

            if packaging is None:
                reason = unavailable_reason or "not_prepared"
                stock = PhysicalCartStockRead(base_units=0, closed_packs=0, intact_blisters=None)
                options = tuple(_unavailable(sale_format, reason) for sale_format in _FORMATS)
            else:
                # One SQL snapshot keeps lots and remainders consistent without
                # blocking stock writers or reserving the previewed quantities.
                try:
                    lots, packs = await self.repository.read_physical_stock(
                        tenant_id, sale.branch_id, catalog_id
                    )
                    available = stock_availability_v2(
                        batches=physical_stock_snapshots_v2(lots, packs),
                        tenant_id=tenant_id,
                        branch_id=sale.branch_id,
                        catalog_id=catalog_id,
                        today=today,
                        packaging=packaging,
                    )
                except ValueError as error:
                    raise BusinessRuleError("Physical stock facts are invalid") from error
                stock = PhysicalCartStockRead(
                    base_units=available.base_units,
                    closed_packs=available.closed_packs,
                    intact_blisters=available.intact_blisters,
                )
                options = tuple(
                    _format_option(catalog, packaging, sale_format) for sale_format in _FORMATS
                )
            return PhysicalCartOptionsRead(
                tenant_id=tenant_id,
                branch_id=sale.branch_id,
                sale_id=sale_id,
                catalog_id=catalog_id,
                stock_date=today,
                packaging=packaging,
                stock=stock,
                options=(options[0], options[1], options[2]),
            )
