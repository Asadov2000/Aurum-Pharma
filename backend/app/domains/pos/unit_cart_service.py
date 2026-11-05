"""Internal unit cart preparation, without stock allocation or payment activation."""

from __future__ import annotations

from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import SerializerFunctionWrapHandler, model_serializer
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.unit_sales import (
    MAX_BASE_UNITS,
    MAX_MONEY,
    SaleFormat,
    UnitSalePreparation,
    quote_unit_sale,
)
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_retail import quote_retail_line_v2
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.models import Sale
from app.domains.pos.unit_cart_models import UnitCartItem
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    FrozenContract,
    Money,
    Position,
    WholeQuantity,
)


class UnitCartItemSnapshot(FrozenContract):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    catalog_id: UUID
    position: Position
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    discount_amount: Money
    physical_packaging: UnitPackagingV2 | None = None

    @model_serializer(mode="wrap")
    def serialize_snapshot(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        result: dict[str, object] = handler(self)
        if self.physical_packaging is None:
            result.pop("physical_packaging", None)
        return result


def _snapshot(item: UnitCartItem) -> UnitCartItemSnapshot:
    return UnitCartItemSnapshot(
        id=item.id,
        tenant_id=item.tenant_id,
        branch_id=item.branch_id,
        sale_id=item.sale_id,
        catalog_id=item.catalog_id,
        position=item.position,
        sale_format=item.sale_format,
        quantity=item.quantity,
        discount_amount=item.discount_amount,
        physical_packaging=(
            UnitPackagingV2.model_validate(item.physical_packaging)
            if item.physical_packaging is not None
            else None
        ),
    )


class UnitCartService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def lock_editable_sale(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> Sale:
        """Lock the cashier's unpaid draft; the caller owns the transaction."""
        if not all(isinstance(value, UUID) for value in (tenant_id, sale_id, actor_id)):
            raise BusinessRuleError("Unit cart identifiers must be UUIDs")
        if not isinstance(allowed_branch_ids, set) or not all(
            isinstance(value, UUID) for value in allowed_branch_ids
        ):
            raise PermissionDeniedError("Explicit branch access is required")
        await self.repository.assert_actor(tenant_id, actor_id)
        sale = await self.repository.lock_sale(tenant_id, sale_id)
        if sale is None:
            raise NotFoundError("Sale draft not found")
        if sale.branch_id not in allowed_branch_ids or sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Sale draft belongs to another cashier or branch")
        await self.repository.assert_actor(tenant_id, actor_id, sale.branch_id)
        if sale.sale_type != "sale" or sale.status != "draft" or sale.currency != "TJS":
            raise BusinessRuleError("Unit cart requires an editable sale draft")
        shift = await self.repository.lock_shift(tenant_id, sale.shift_id)
        if (
            shift is None
            or shift.status != "open"
            or shift.branch_id != sale.branch_id
            or shift.register_id != sale.register_id
            or shift.opened_by_user_id != actor_id
            or shift.currency != "TJS"
        ):
            raise BusinessRuleError("Unit cart requires the cashier's open shift")
        if not await self.repository.active_register(tenant_id, sale.branch_id, sale.register_id):
            raise BusinessRuleError("Sale register is inactive")
        if await self.repository.has_payment_activity(tenant_id, sale_id):
            raise ConflictError("Unit cart cannot change after payment activity")
        return sale

    @staticmethod
    def _validate_price(
        catalog: TenantCatalog, sale_format: SaleFormat, quantity: int, discount_amount: Decimal
    ) -> None:
        if not catalog.is_active or catalog.deleted_at is not None or catalog.currency != "TJS":
            raise BusinessRuleError("Catalog item is not available for unit checkout")
        try:
            preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
            quote = quote_unit_sale(
                preparation,
                quantity=Decimal(quantity),
                sale_format=sale_format,
                pack_price=catalog.base_price,
            )
        except (ValueError, PydanticValidationError) as error:
            raise BusinessRuleError(
                "Valid unit packaging and explicit retail prices are required"
            ) from error
        if discount_amount >= quote.total_price:
            raise BusinessRuleError("Unit cart discount must leave a positive amount")

    async def upsert_item(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        item_id: UUID,
        catalog_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        position: int,
        sale_format: SaleFormat,
        quantity: int,
        discount_amount: Decimal = Decimal("0.00"),
    ) -> UnitCartItemSnapshot:
        return await self._upsert_item(
            tenant_id=tenant_id,
            sale_id=sale_id,
            item_id=item_id,
            catalog_id=catalog_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            position=position,
            sale_format=sale_format,
            quantity=quantity,
            discount_amount=discount_amount,
            physical=False,
        )

    async def upsert_physical_item(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        item_id: UUID,
        catalog_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        position: int,
        sale_format: SaleFormatV2,
        quantity: int,
        discount_amount: Decimal = Decimal("0.00"),
    ) -> UnitCartItemSnapshot:
        """Capture composition in an unpaid draft, never reserve or dispense stock."""
        return await self._upsert_item(
            tenant_id=tenant_id,
            sale_id=sale_id,
            item_id=item_id,
            catalog_id=catalog_id,
            actor_id=actor_id,
            allowed_branch_ids=allowed_branch_ids,
            position=position,
            sale_format=sale_format,
            quantity=quantity,
            discount_amount=discount_amount,
            physical=True,
        )

    async def _upsert_item(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        item_id: UUID,
        catalog_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        position: int,
        sale_format: SaleFormatV2,
        quantity: int,
        discount_amount: Decimal,
        physical: bool,
    ) -> UnitCartItemSnapshot:
        self._validate_item_identity(item_id, catalog_id, position)
        if (
            type(quantity) is not int
            or not 0 < quantity <= MAX_BASE_UNITS
            or sale_format not in (("pack", "blister", "unit") if physical else ("pack", "unit"))
        ):
            raise BusinessRuleError("Unit cart requires a valid format and whole positive quantity")
        self._validate_discount(discount_amount)
        async with self.repository.session.begin_nested():
            sale = await self.lock_editable_sale(tenant_id, sale_id, actor_id, allowed_branch_ids)
            item = await self.repository.get_item(tenant_id, item_id)
            if item is not None and (
                item.sale_id != sale_id
                or item.branch_id != sale.branch_id
                or item.catalog_id != catalog_id
            ):
                raise ConflictError(
                    "Unit cart item identity belongs to another draft or catalog item"
                )
            if item is not None and (item.physical_packaging is not None) != physical:
                raise ConflictError("Unit cart accounting mode cannot change")
            catalog = await self.repository.lock_catalog(tenant_id, catalog_id)
            if catalog is None:
                raise NotFoundError("Catalog item not found")
            items = await self.repository.list_items(tenant_id, sale_id)
            if any((row.physical_packaging is not None) != physical for row in items):
                raise ConflictError("Prepared and physical unit cart rows cannot be mixed")
            packaging: UnitPackagingV2 | None = None
            if physical:
                packaging = self._physical_packaging(
                    catalog, item, item_id, sale_format, quantity, discount_amount
                )
            else:
                # The legacy method admitted only these two formats above.
                legacy_format: SaleFormat = "pack" if sale_format == "pack" else "unit"
                self._validate_price(catalog, legacy_format, quantity, discount_amount)
            if any(row.position == position and row.id != item_id for row in items):
                raise ConflictError("Unit cart position is already occupied")
            if item is None:
                if len(items) >= 200:
                    raise BusinessRuleError("Unit cart cannot exceed 200 items")
                item = UnitCartItem(
                    id=item_id,
                    tenant_id=tenant_id,
                    branch_id=sale.branch_id,
                    sale_id=sale_id,
                    catalog_id=catalog_id,
                    position=position,
                    sale_format=sale_format,
                    quantity=quantity,
                    discount_amount=discount_amount,
                    physical_packaging=packaging.model_dump(mode="json") if packaging else None,
                )
            else:
                item.position = position
                item.sale_format = sale_format
                item.quantity = quantity
                item.discount_amount = discount_amount
            await self.repository.save_item(item)
            return _snapshot(item)

    @staticmethod
    def _validate_item_identity(item_id: UUID, catalog_id: UUID, position: int) -> None:
        if not isinstance(item_id, UUID) or not isinstance(catalog_id, UUID):
            raise BusinessRuleError("Unit cart identifiers must be UUIDs")
        if type(position) is not int or not 1 <= position <= 200:
            raise BusinessRuleError("Unit cart position must be between 1 and 200")

    @staticmethod
    def _validate_discount(discount_amount: Decimal) -> None:
        if (
            not isinstance(discount_amount, Decimal)
            or not discount_amount.is_finite()
            or not 0 <= discount_amount <= MAX_MONEY
        ):
            raise BusinessRuleError("Unit cart discount must be a valid money amount")
        with localcontext(EXACT_CONTEXT):
            if discount_amount != discount_amount.quantize(Decimal("0.01")):
                raise BusinessRuleError(
                    "Unit cart discount must contain at most two decimal places"
                )

    @staticmethod
    def _physical_packaging(
        catalog: TenantCatalog,
        item: UnitCartItem | None,
        item_id: UUID,
        sale_format: SaleFormatV2,
        quantity: int,
        discount_amount: Decimal,
    ) -> UnitPackagingV2:
        try:
            preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
            packaging = UnitPackagingV2(
                unit=preparation.unit,
                units_per_pack=preparation.units_per_pack,
                units_per_blister=preparation.units_per_blister,
                dispense_step=preparation.dispense_step,
            )
            if (
                item is not None
                and UnitPackagingV2.model_validate(item.physical_packaging) != packaging
            ):
                raise ConflictError("Physical cart composition is stale; recreate the position")
            quote_retail_line_v2(
                UnitCartLineV2(
                    id=item_id,
                    catalog_id=catalog.id,
                    packaging=packaging,
                    sale_format=sale_format,
                    quantity=quantity,
                ),
                catalog,
                discount_amount,
            )
            return packaging
        except ValueError as error:
            raise BusinessRuleError(
                "Valid physical packaging and retail prices are required"
            ) from error

    async def remove_item(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        item_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> None:
        if not isinstance(item_id, UUID):
            raise BusinessRuleError("Unit cart item identifier must be a UUID")
        try:
            async with self.repository.session.begin_nested():
                await self.lock_editable_sale(tenant_id, sale_id, actor_id, allowed_branch_ids)
                item = await self.repository.get_item(tenant_id, item_id)
                if item is None:
                    raise NotFoundError("Unit cart item not found")
                if item.sale_id != sale_id:
                    raise ConflictError("Unit cart item belongs to another draft")
                await self.repository.delete_item(item)
        except IntegrityError as error:
            if "fk_prescription_unit_cart_item" in str(error.orig):
                raise BusinessRuleError(
                    "Remove the linked prescription before removing this cart position"
                ) from error
            raise

    async def list_items(
        self,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
    ) -> tuple[UnitCartItemSnapshot, ...]:
        async with self.repository.session.begin_nested():
            await self.lock_editable_sale(tenant_id, sale_id, actor_id, allowed_branch_ids)
            return tuple(
                _snapshot(item) for item in await self.repository.list_items(tenant_id, sale_id)
            )
