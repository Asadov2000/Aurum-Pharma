"""Read projections and narrow database commands; never commit or send an event."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.retail_checkout_event_models import (
    RetailCheckoutEventSnapshot,
    RetailEventCaptureState,
)
from app.domains.sync.repository import ReservedStreamPosition


class RetailCheckoutEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutEventSnapshot | None:
        result = await self.session.execute(
            text(
                "SELECT tenant_id,sale_id,id AS operation_id,event_id,source_kind,branch_id,"
                "register_id,cashier_id,origin_node_id,writer_epoch,sequence,"
                "previous_source_checksum,previous_projection_checksum,event_fingerprint,record "
                "FROM public.pos_retail_checkout_event "
                "WHERE tenant_id=:tenant AND sale_id=:sale AND id=:operation"
            ),
            {"tenant": tenant_id, "sale": sale_id, "operation": operation_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return RetailCheckoutEventSnapshot(**dict(row))

    async def capture_state(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailEventCaptureState | None:
        result = await self.session.execute(
            text(
                "SELECT event_capture_required AS required,"
                "coalesce(source_transaction_id=txid_current_if_assigned(),false) AS current_tx "
                "FROM public.pos_retail_checkout_origin "
                "WHERE tenant_id=:tenant AND sale_id=:sale AND id=:operation"
            ),
            {"tenant": tenant_id, "sale": sale_id, "operation": operation_id},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return RetailEventCaptureState(bool(row["required"]), bool(row["current_tx"]))

    async def reserve(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> ReservedStreamPosition:
        result = await self.session.execute(
            text(
                "SELECT * FROM public.reserve_retail_checkout_event_position("
                ":tenant,:sale,:operation)"
            ),
            {"tenant": tenant_id, "sale": sale_id, "operation": operation_id},
        )
        row = result.mappings().one()
        return ReservedStreamPosition(
            stream_id=row["stream_id"],
            origin_node_id=row["origin_node_id"],
            writer_epoch=int(row["writer_epoch"]),
            sequence=int(row["sequence"]),
            previous_checksum=str(row["previous_checksum"]),
            previous_projection_checksum=str(row["previous_projection_checksum"]),
        )

    async def store(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        *,
        record: bytes,
        previous_source_checksum: str,
        previous_projection_checksum: str,
        event_fingerprint: str,
    ) -> None:
        await self.session.execute(
            text(
                "SELECT public.store_retail_checkout_event("
                ":tenant,:sale,:operation,:record,:source,:projection,:fingerprint)"
            ),
            {
                "tenant": tenant_id,
                "sale": sale_id,
                "operation": operation_id,
                "record": record,
                "source": previous_source_checksum,
                "projection": previous_projection_checksum,
                "fingerprint": event_fingerprint,
            },
        )

    async def validate_completion(self) -> None:
        await self.session.execute(
            text("SET CONSTRAINTS trg_retail_checkout_event_required IMMEDIATE")
        )
        await self.session.execute(
            text("SET CONSTRAINTS trg_retail_checkout_event_required DEFERRED")
        )
