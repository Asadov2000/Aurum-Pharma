"""Current mixed-cart retail quote; never an allocation, receipt or accepted payment."""

from __future__ import annotations

import json
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, ValidationError, model_validator

from app.core.errors import BusinessRuleError, NotFoundError
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.unit_sales import (
    MAX_BASE_UNITS,
    SaleFormat,
    UnitSalePreparation,
    quote_unit_sale,
)
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    AmountsV2,
    FrozenContract,
    LegacyResultLineV2,
    Money,
    Position,
    WholeQuantity,
)


class UnitDraftQuoteLineV2(AmountsV2):
    inventory_kind: Literal["prepared_unit"] = "prepared_unit"
    id: UUID
    position: Position
    catalog_id: UUID
    packaging: UnitPackaging
    sale_format: SaleFormat
    quantity: WholeQuantity
    base_units: WholeQuantity

    @model_validator(mode="after")
    def original_arithmetic(self) -> Self:
        units = self.quantity
        if self.sale_format == "pack":
            units *= self.packaging.units_per_pack
        elif self.quantity % self.packaging.dispense_step:
            raise ValueError("quantity differs from the quoted dispense step")
        with localcontext(EXACT_CONTEXT):
            gross = self.original_retail_unit_price * self.quantity
        if units != self.base_units or units > MAX_BASE_UNITS or gross != self.gross_amount:
            raise ValueError("quoted packaging, quantity and amounts differ")
        if self.net_amount <= 0:
            raise ValueError("unit draft discount must leave a positive net amount")
        return self


DraftQuoteLineV2 = Annotated[
    LegacyResultLineV2 | UnitDraftQuoteLineV2, Field(discriminator="inventory_kind")
]


class MixedCartQuoteV2(FrozenContract):
    schema_version: Literal[2] = 2
    state: Literal["draft_quote"] = "draft_quote"
    tenant_id: UUID
    branch_id: UUID
    sale_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_id: UUID
    lines: tuple[DraftQuoteLineV2, ...] = Field(min_length=1, max_length=200)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def document_balance(self) -> Self:
        if len({line.id for line in self.lines}) != len(self.lines):
            raise ValueError("mixed cart source identifiers must be unique")
        if tuple(line.position for line in self.lines) != tuple(range(1, len(self.lines) + 1)):
            raise ValueError("mixed cart positions must be contiguous")
        with localcontext(EXACT_CONTEXT):
            total = sum((line.net_amount for line in self.lines), Decimal("0.00"))
        if total != self.total_amount or self.total_amount <= 0:
            raise ValueError("mixed cart total must match its positive line sum")
        return self

    @property
    def revision_hash(self) -> str:
        # Stale-quote comparison only. This hash does not authorize checkout.
        payload = self.model_dump(mode="json")
        return sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        ).hexdigest()


class UnitCartQuoteService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def quote(
        self, *, tenant_id: UUID, sale_id: UUID, actor_id: UUID, allowed_branch_ids: set[UUID]
    ) -> MixedCartQuoteV2:
        async with self.repository.session.begin_nested():
            sale = await UnitCartService(self.repository).lock_editable_sale(
                tenant_id, sale_id, actor_id, allowed_branch_ids
            )
            legacy = await self.repository.list_legacy_items(tenant_id, sale_id)
            units = await self.repository.list_items(tenant_id, sale_id)
            if not 1 <= len(legacy) + len(units) <= 200:
                raise BusinessRuleError("Mixed cart must contain between one and 200 positions")
            lines: list[LegacyResultLineV2 | UnitDraftQuoteLineV2] = []
            try:
                for row in legacy:
                    if row.parent_sale_item_id is not None or row.currency != "TJS":
                        raise BusinessRuleError("Legacy draft row has incompatible retail facts")
                    with localcontext(EXACT_CONTEXT):
                        gross = (row.unit_price * row.qty).quantize(Decimal("0.01"))
                    lines.append(
                        LegacyResultLineV2(
                            inventory_kind="legacy_pack",
                            id=row.id,
                            position=len(lines) + 1,
                            catalog_id=row.catalog_id,
                            batch_id=row.batch_id,
                            quantity=row.qty,
                            original_retail_unit_price=row.unit_price,
                            gross_amount=gross,
                            discount_amount=row.discount_amount,
                            net_amount=row.total_price,
                        )
                    )
                # One stable order for catalog locks, including repeated goods.
                catalogs: dict[UUID, TenantCatalog] = {}
                for catalog_id in sorted({row.catalog_id for row in units}, key=str):
                    catalog = await self.repository.lock_catalog(tenant_id, catalog_id)
                    if catalog is None:
                        raise NotFoundError("Unit cart catalog item not found")
                    catalogs[catalog_id] = catalog
                for item in units:
                    catalog = catalogs[item.catalog_id]
                    UnitCartService._validate_price(
                        catalog, item.sale_format, item.quantity, item.discount_amount
                    )
                    preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
                    quoted = quote_unit_sale(
                        preparation,
                        quantity=Decimal(item.quantity),
                        sale_format=item.sale_format,
                        pack_price=catalog.base_price,
                    )
                    with localcontext(EXACT_CONTEXT):
                        net = quoted.total_price - item.discount_amount
                    lines.append(
                        UnitDraftQuoteLineV2(
                            id=item.id,
                            position=len(lines) + 1,
                            catalog_id=item.catalog_id,
                            packaging=UnitPackaging(
                                version=preparation.version,
                                unit=preparation.unit,
                                units_per_pack=preparation.units_per_pack,
                                dispense_step=preparation.dispense_step,
                            ),
                            sale_format=item.sale_format,
                            quantity=item.quantity,
                            base_units=quoted.base_units,
                            original_retail_unit_price=quoted.unit_price,
                            gross_amount=quoted.total_price,
                            discount_amount=item.discount_amount,
                            net_amount=net,
                        )
                    )
                with localcontext(EXACT_CONTEXT):
                    total = sum((line.net_amount for line in lines), Decimal("0.00"))
                return MixedCartQuoteV2(
                    tenant_id=tenant_id,
                    branch_id=sale.branch_id,
                    sale_id=sale.id,
                    register_id=sale.register_id,
                    shift_id=sale.shift_id,
                    cashier_id=actor_id,
                    lines=tuple(lines),
                    total_amount=total,
                )
            except (ValidationError, ValueError, ArithmeticError) as error:
                raise BusinessRuleError(
                    "Mixed cart has invalid or inconsistent retail amounts"
                ) from error
