"""Read scalar checkout-source metadata without refreshing ORM identities."""

from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout


@dataclass(frozen=True, slots=True)
class RetailCheckoutMarker:
    kind: Literal["physical", "mixed"]
    operation_id: UUID
    cash_intent_id: UUID | None
    payment_plan_intent_id: UUID | None
    payment_journal_version: int


@dataclass(frozen=True, slots=True)
class RetailCheckoutSourceSnapshot:
    tenant_id: UUID
    sale_id: UUID
    branch_id: UUID
    cashier_id: UUID
    status: str
    is_test: bool
    sale_type: str
    parent_sale_id: UUID | None
    currency: str
    physical: RetailCheckoutMarker | None
    mixed: RetailCheckoutMarker | None


class RetailCheckoutSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutSourceSnapshot | None:
        statement = (
            select(
                Sale.tenant_id.label("tenant_id"),
                Sale.id.label("sale_id"),
                Sale.branch_id.label("branch_id"),
                Sale.cashier_user_id.label("cashier_id"),
                Sale.status.label("status"),
                Sale.is_test.label("is_test"),
                Sale.sale_type.label("sale_type"),
                Sale.parent_sale_id.label("parent_sale_id"),
                Sale.currency.label("currency"),
                PhysicalCashCheckout.id.label("physical_id"),
                PhysicalCashCheckout.cash_intent_id.label("physical_cash_intent_id"),
                PhysicalCashCheckout.payment_plan_intent_id.label("physical_plan_intent_id"),
                PhysicalCashCheckout.payment_journal_version.label("physical_version"),
                MixedCashCheckout.id.label("mixed_id"),
                MixedCashCheckout.cash_intent_id.label("mixed_cash_intent_id"),
                MixedCashCheckout.payment_plan_intent_id.label("mixed_plan_intent_id"),
                MixedCashCheckout.payment_journal_version.label("mixed_version"),
            )
            .select_from(Sale)
            .outerjoin(
                PhysicalCashCheckout,
                and_(
                    PhysicalCashCheckout.tenant_id == Sale.tenant_id,
                    PhysicalCashCheckout.sale_id == Sale.id,
                ),
            )
            .outerjoin(
                MixedCashCheckout,
                and_(
                    MixedCashCheckout.tenant_id == Sale.tenant_id,
                    MixedCashCheckout.sale_id == Sale.id,
                ),
            )
            .where(
                Sale.tenant_id == tenant_id,
                Sale.id == sale_id,
                or_(
                    PhysicalCashCheckout.id == operation_id,
                    MixedCashCheckout.id == operation_id,
                    and_(PhysicalCashCheckout.id.is_(None), MixedCashCheckout.id.is_(None)),
                ),
            )
            .execution_options(autoflush=False)
        )
        row = (await self.session.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        # Scalar column types come from these mapped columns, not arbitrary JSON.
        # Outer joins alone make journal IDs/metadata nullable. Once its ID is
        # present, the journal's NOT NULL version remains an integer.
        physical_id = cast(UUID | None, row["physical_id"])
        mixed_id = cast(UUID | None, row["mixed_id"])
        physical = (
            RetailCheckoutMarker(
                kind="physical",
                operation_id=physical_id,
                cash_intent_id=cast(UUID | None, row["physical_cash_intent_id"]),
                payment_plan_intent_id=cast(UUID | None, row["physical_plan_intent_id"]),
                payment_journal_version=cast(int, row["physical_version"]),
            )
            if physical_id is not None
            else None
        )
        mixed = (
            RetailCheckoutMarker(
                kind="mixed",
                operation_id=mixed_id,
                cash_intent_id=cast(UUID | None, row["mixed_cash_intent_id"]),
                payment_plan_intent_id=cast(UUID | None, row["mixed_plan_intent_id"]),
                payment_journal_version=cast(int, row["mixed_version"]),
            )
            if mixed_id is not None
            else None
        )
        return RetailCheckoutSourceSnapshot(
            tenant_id=cast(UUID, row["tenant_id"]),
            sale_id=cast(UUID, row["sale_id"]),
            branch_id=cast(UUID, row["branch_id"]),
            cashier_id=cast(UUID, row["cashier_id"]),
            status=cast(str, row["status"]),
            is_test=cast(bool, row["is_test"]),
            sale_type=cast(str, row["sale_type"]),
            parent_sale_id=cast(UUID | None, row["parent_sale_id"]),
            currency=cast(str, row["currency"]),
            physical=physical,
            mixed=mixed,
        )
