"""Read historical origin metadata without consulting the current writer pointer."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.retail_checkout_origin_models import RetailCheckoutOrigin


@dataclass(frozen=True, slots=True)
class RetailCheckoutOriginSnapshot:
    tenant_id: UUID
    operation_id: UUID
    sale_id: UUID
    source_kind: Literal["physical", "mixed"]
    branch_id: UUID
    register_id: UUID
    cashier_id: UUID
    origin_node_id: UUID
    writer_epoch: int
    created_at: datetime
    created_by: UUID


class RetailCheckoutOriginRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutOriginSnapshot | None:
        row = (
            (
                await self.session.execute(
                    select(
                        RetailCheckoutOrigin.tenant_id,
                        RetailCheckoutOrigin.operation_id.label("operation_id"),
                        RetailCheckoutOrigin.sale_id,
                        RetailCheckoutOrigin.source_kind,
                        RetailCheckoutOrigin.branch_id,
                        RetailCheckoutOrigin.register_id,
                        RetailCheckoutOrigin.cashier_user_id,
                        RetailCheckoutOrigin.origin_node_id,
                        RetailCheckoutOrigin.writer_epoch,
                        RetailCheckoutOrigin.created_at,
                        RetailCheckoutOrigin.created_by,
                    )
                    .where(
                        RetailCheckoutOrigin.tenant_id == tenant_id,
                        RetailCheckoutOrigin.sale_id == sale_id,
                        RetailCheckoutOrigin.operation_id == operation_id,
                    )
                    .execution_options(autoflush=False)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        # These are scalar mapped columns; the database fixes their types and
        # constrains source_kind. No arbitrary document or current stream is read.
        return RetailCheckoutOriginSnapshot(
            tenant_id=cast(UUID, row["tenant_id"]),
            operation_id=cast(UUID, row["operation_id"]),
            sale_id=cast(UUID, row["sale_id"]),
            source_kind=cast(Literal["physical", "mixed"], row["source_kind"]),
            branch_id=cast(UUID, row["branch_id"]),
            register_id=cast(UUID, row["register_id"]),
            cashier_id=cast(UUID, row["cashier_user_id"]),
            origin_node_id=cast(UUID, row["origin_node_id"]),
            writer_epoch=cast(int, row["writer_epoch"]),
            created_at=cast(datetime, row["created_at"]),
            created_by=cast(UUID, row["created_by"]),
        )
