"""Internal prepared line posting; no live checkout, payments or shifts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.exc import IntegrityError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.catalog.unit_sales import (
    MAX_BASE_UNITS,
    MAX_MONEY,
    DispenseUnit,
    SaleFormat,
    UnitSalePreparation,
    quote_unit_sale,
)
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import PreparedUnitLedgerService, UnitStockCostSlice
from app.domains.inventory.unit_stock import UnitPackaging
from app.domains.pos.unit_line_models import UnitSaleCommand, UnitSaleLine
from app.domains.pos.unit_line_repository import UnitSaleLineRepository
from app.domains.pos.unit_money import allocate_unit_amount


@dataclass(frozen=True)
class UnitSaleLineSnapshot:
    id: UUID
    command_id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    stock_operation_id: UUID
    version: int
    unit: DispenseUnit
    units_per_pack: int
    dispense_step: int
    sale_format: SaleFormat
    quantity: int
    base_units: int
    original_retail_unit_price: Decimal
    gross_amount: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    original_cost_amount: Decimal
    money_version: Literal[1]
    currency: Literal["TJS"]


@dataclass(frozen=True)
class PreparedUnitSaleLineResult:
    """Internal prepared snapshot, never a receipt or completed checkout."""

    snapshot: UnitSaleLineSnapshot
    cost_slices: tuple[UnitStockCostSlice, ...]
    state: Literal["prepared"] = "prepared"


def _fingerprint(
    tenant_id: UUID,
    branch_id: UUID,
    catalog_id: UUID,
    sale_format: SaleFormat,
    quantity: int,
    discount: Decimal,
) -> str:
    payload = {
        "version": 1,
        "tenant_id": str(tenant_id),
        "branch_id": str(branch_id),
        "catalog_id": str(catalog_id),
        "sale_format": sale_format,
        "quantity": quantity,
        "discount_amount": format(discount, ".2f"),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PreparedUnitSaleLineService:
    def __init__(self, repo: UnitSaleLineRepository) -> None:
        self.repo = repo
        self.stock_repo = UnitLedgerRepository(repo.session)
        self.stock_service = PreparedUnitLedgerService(self.stock_repo)

    async def post_line(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        operation_id: UUID,
        sale_format: SaleFormat,
        quantity: int,
        today: date,
        allowed_branch_ids: set[UUID],
        discount_amount: Decimal = Decimal("0.00"),
    ) -> PreparedUnitSaleLineResult:
        if not isinstance(allowed_branch_ids, set) or branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")
        if type(quantity) is not int or not 1 <= quantity <= MAX_BASE_UNITS:
            raise ValueError("quantity must be a positive whole integer within the stock limit")
        if sale_format not in ("pack", "unit"):
            raise ValueError("unknown sale format")
        discount = allocate_unit_amount(
            discount_amount,
            total_units=1,
            offset=0,
            quantity=1,
        ).total_amount
        request_hash = _fingerprint(
            tenant_id, branch_id, catalog_id, sale_format, quantity, discount
        )
        try:
            async with self.repo.session.begin_nested():
                command, created = await self.repo.claim_command(
                    UnitSaleCommand(
                        id=operation_id,
                        tenant_id=tenant_id,
                        branch_id=branch_id,
                        catalog_id=catalog_id,
                        request_hash=request_hash,
                        sale_format=sale_format,
                        quantity=quantity,
                        discount_amount=discount,
                    )
                )
                if command.request_hash != request_hash:
                    raise ConflictError("Operation was already used for a different request")
                if created:
                    await self._post(command, today, allowed_branch_ids)
                return await self._result(command)
        except IntegrityError as error:
            # Translate only the explicit identity conflict after the savepoint
            # has rolled back; unrelated database constraints remain visible.
            if "pos_unit_line_stock_already_recorded" in str(error.orig):
                raise ConflictError("Stock operation was already recorded") from error
            raise

    async def _post(
        self,
        command: UnitSaleCommand,
        today: date,
        allowed_branch_ids: set[UUID],
    ) -> None:
        catalog = await self.repo.get_catalog(command.tenant_id, command.catalog_id)
        if catalog is None:
            raise NotFoundError("Catalog item not found")
        if (
            not catalog.is_active
            or catalog.deleted_at is not None
            or catalog.currency != "TJS"
            or catalog.unit_sale_preparation is None
        ):
            raise BusinessRuleError("Catalog item is not prepared for unit sale posting")
        preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
        packaging = UnitPackaging(
            version=preparation.version,
            unit=preparation.unit,
            units_per_pack=preparation.units_per_pack,
            dispense_step=preparation.dispense_step,
        )
        stock_operation_id = uuid5(
            NAMESPACE_URL,
            f"aurum-unit-line-stock:{command.tenant_id}:{command.id}",
        )
        stock = await self.stock_service.dispense(
            tenant_id=command.tenant_id,
            branch_id=command.branch_id,
            catalog_id=command.catalog_id,
            operation_id=stock_operation_id,
            packaging=packaging,
            sale_format=command.sale_format,
            quantity=command.quantity,
            today=today,
            allowed_branch_ids=allowed_branch_ids,
            require_new_operation=True,
        )
        catalog = await self.stock_repo.lock_catalog(command.tenant_id, command.catalog_id)
        if catalog is None:
            raise NotFoundError("Catalog item not found")
        if (
            not catalog.is_active
            or catalog.deleted_at is not None
            or catalog.currency != "TJS"
            or catalog.unit_sale_preparation is None
        ):
            raise BusinessRuleError("Catalog item is not prepared for unit sale posting")
        locked_preparation = UnitSalePreparation.model_validate(catalog.unit_sale_preparation)
        if (
            locked_preparation.version,
            locked_preparation.unit,
            locked_preparation.units_per_pack,
            locked_preparation.dispense_step,
        ) != (
            packaging.version,
            packaging.unit,
            packaging.units_per_pack,
            packaging.dispense_step,
        ):
            raise ConflictError("Catalog packaging changed during line posting")
        locked_preparation = locked_preparation.model_copy(
            update={
                "unit_price": allocate_unit_amount(
                    locked_preparation.unit_price,
                    total_units=1,
                    offset=0,
                    quantity=1,
                ).total_amount,
            }
        )
        quote = quote_unit_sale(
            locked_preparation,
            quantity=Decimal(command.quantity),
            sale_format=command.sale_format,
            pack_price=catalog.base_price,
        )
        if command.discount_amount >= quote.total_price:
            raise ValueError("discount must leave a positive net amount")
        entries = await self.stock_repo.get_entries(command.tenant_id, stock_operation_id)
        if not entries or any(
            entry.cost_version != 1 or entry.cost_amount is None for entry in entries
        ):
            raise BusinessRuleError("Prepared stock cost is unknown")
        with localcontext() as context:
            context.prec = 40
            cost = sum((part.amount for part in stock.cost_slices), Decimal("0.00"))
            if cost > MAX_MONEY:
                raise BusinessRuleError("Prepared stock cost exceeds the money limit")
            net = quote.total_price - command.discount_amount
        await self.repo.insert_line(
            UnitSaleLine(
                id=uuid5(NAMESPACE_URL, f"aurum-unit-line:{command.tenant_id}:{command.id}"),
                tenant_id=command.tenant_id,
                branch_id=command.branch_id,
                catalog_id=command.catalog_id,
                command_id=command.id,
                stock_operation_id=stock_operation_id,
                version=packaging.version,
                unit=packaging.unit,
                units_per_pack=packaging.units_per_pack,
                dispense_step=packaging.dispense_step,
                sale_format=command.sale_format,
                quantity=command.quantity,
                base_units=stock.base_units,
                original_retail_unit_price=quote.unit_price,
                gross_amount=quote.total_price,
                discount_amount=command.discount_amount,
                net_amount=net,
                original_cost_amount=cost,
                money_version=1,
                currency="TJS",
            )
        )

    async def _result(self, command: UnitSaleCommand) -> PreparedUnitSaleLineResult:
        line = await self.repo.get_line(command.tenant_id, command.id)
        if (
            line is None
            or line.branch_id != command.branch_id
            or line.catalog_id != command.catalog_id
        ):
            raise BusinessRuleError("Prepared command has no complete line result")
        entries = await self.stock_repo.get_entries(command.tenant_id, line.stock_operation_id)
        slices: list[UnitStockCostSlice] = []
        for entry in entries:
            if (
                entry.cost_version != 1
                or entry.cost_pack_ordinal_start is None
                or entry.cost_pack_count is None
                or entry.cost_unit_offset is None
                or entry.cost_base_units is None
                or entry.cost_original_pack_cost is None
                or entry.cost_amount is None
            ):
                raise BusinessRuleError("Prepared line has an invalid cost snapshot")
            slices.append(
                UnitStockCostSlice(
                    entry.id,
                    entry.lot_id,
                    entry.cost_pack_ordinal_start,
                    entry.cost_pack_count,
                    entry.cost_unit_offset,
                    entry.cost_base_units,
                    entry.cost_original_pack_cost,
                    entry.cost_amount,
                )
            )
        with localcontext() as context:
            context.prec = 40
            cost = sum((part.amount for part in slices), Decimal("0.00"))
        if (
            not entries
            or sum(part.base_units for part in slices) != line.base_units
            or cost != line.original_cost_amount
        ):
            raise BusinessRuleError("Prepared line has incomplete cost allocations")
        if line.money_version != 1 or line.currency != "TJS":
            raise BusinessRuleError("Prepared line has an unsupported money snapshot")
        return PreparedUnitSaleLineResult(
            UnitSaleLineSnapshot(
                line.id,
                line.command_id,
                line.tenant_id,
                line.branch_id,
                line.catalog_id,
                line.stock_operation_id,
                line.version,
                line.unit,
                line.units_per_pack,
                line.dispense_step,
                line.sale_format,
                line.quantity,
                line.base_units,
                line.original_retail_unit_price,
                line.gross_amount,
                line.discount_amount,
                line.net_amount,
                line.original_cost_amount,
                1,
                "TJS",
            ),
            tuple(slices),
        )
