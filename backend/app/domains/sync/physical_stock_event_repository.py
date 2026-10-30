"""Read protected source evidence and reconstruct a scoped immutable read view."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.pos.retail_sale_event_storage import RetailSaleEventAnchor
from app.domains.sync.models import SyncInboxEvent
from app.domains.sync.physical_read_snapshot import PhysicalRecord
from app.domains.sync.physical_stock_events import (
    PhysicalStockConfig,
    PhysicalStockEventAnchor,
    decode_physical_stock_event,
    encode_physical_stock_event,
)
from app.domains.sync.physical_stock_projection import (
    MAX_PROJECTION_EVENTS,
    PhysicalStockBaselineAnchor,
    PhysicalStockProjection,
    reduce_physical_stock_projection,
)
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncEventEnvelope
from app.domains.sync.service import SyncEdgeApplyService


class PhysicalStockEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, *, tenant_id: UUID, event_id: UUID
    ) -> tuple[SyncEventEnvelope, PhysicalStockEventAnchor]:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT envelope,tenant_id,branch_id,origin_node_id,writer_epoch,sequence,"
                        "previous_source_checksum,previous_projection_checksum,event_fingerprint "
                        "FROM public.sync_physical_stock_event "
                        "WHERE tenant_id=:tenant AND id=:event"
                    ).columns(envelope=JSONB()),
                    {"tenant": tenant_id, "event": event_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("Physical stock event not found")
        envelope = SyncEventEnvelope.model_validate(row["envelope"])
        anchor = PhysicalStockEventAnchor.model_validate(
            {name: row[name] for name in PhysicalStockEventAnchor.model_fields}
        )
        decode_physical_stock_event(
            encode_physical_stock_event(envelope, anchor=anchor), anchor=anchor
        )
        return envelope, anchor


async def _financial_anchor(
    repository: SyncEdgeRepository, event: SyncEventEnvelope, edge_node_id: UUID
) -> RetailSaleEventAnchor:
    reader = (
        repository.get_retail_refund_anchor
        if event.schema_version == 6
        else repository.get_retail_event_anchor
    )
    saved = await reader(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        edge_node_id=edge_node_id,
    )
    if saved is None:
        raise ValueError("Financial source lacks its independent anchor")
    if event.schema_version == 6:
        await SyncEdgeApplyService(repository).validate_refund_projection(event)
    return RetailSaleEventAnchor.model_validate(saved, from_attributes=True)


async def materialize_physical_stock_projection(
    repository: SyncEdgeRepository,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    actor_id: UUID,
    edge_node_id: UUID,
    snapshot_id: UUID,
    physical_records: Sequence[PhysicalRecord],
    configuration: Sequence[PhysicalStockConfig],
) -> PhysicalStockProjection:
    """Explicit internal reader; the protected baseline authenticates caller data.

    The snapshot's revision number is deliberately never used as stream sequence.
    The owner must bind the independently verified physical/configuration hashes
    to an exact stream checkpoint before this reader can produce a ready view.
    """
    allowed = await repository.session.scalar(
        text(
            "SELECT current_user='aurum_app' AND session_user='aurum_app' "
            "AND NOT public.is_support_session() AND NOT public.is_tenant_support_session() "
            "AND public.current_tenant_id()=:tenant AND public.current_app_user_id()=:actor "
            "AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active') "
            "AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch) "
            "AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view_costs',:branch)"
        ),
        {"tenant": tenant_id, "branch": branch_id, "actor": actor_id},
    )
    if allowed is not True:
        raise PermissionDeniedError("Physical projection historical cost permission denied")
    if await repository.get_local_edge_node_id() != edge_node_id:
        raise PermissionDeniedError("Physical projection Edge scope mismatch")
    saved = await repository.get_physical_stock_baseline(
        tenant_id=tenant_id,
        branch_id=branch_id,
        edge_node_id=edge_node_id,
        snapshot_id=snapshot_id,
    )
    if saved is None:
        raise NotFoundError("Trusted physical baseline is unavailable")
    anchor = PhysicalStockBaselineAnchor.model_validate(saved, from_attributes=True)
    cursor = await repository.get_cursor(
        tenant_id=tenant_id,
        branch_id=branch_id,
        origin_node_id=anchor.origin_node_id,
        writer_epoch=anchor.writer_epoch,
        for_update=False,
    )
    if cursor is None or cursor.last_sequence < anchor.sequence:
        raise ValueError("Physical projection cursor precedes its trusted baseline")
    rows = list(
        await repository.session.scalars(
            select(SyncInboxEvent)
            .where(
                SyncInboxEvent.tenant_id == tenant_id,
                SyncInboxEvent.branch_id == branch_id,
                SyncInboxEvent.origin_node_id == anchor.origin_node_id,
                SyncInboxEvent.writer_epoch == anchor.writer_epoch,
                SyncInboxEvent.sequence > anchor.sequence,
                SyncInboxEvent.sequence <= cursor.last_sequence,
                SyncInboxEvent.status == "applied",
            )
            .order_by(SyncInboxEvent.sequence)
            .limit(MAX_PROJECTION_EVENTS + 1)
        )
    )
    if len(rows) > MAX_PROJECTION_EVENTS:
        raise ValueError("Physical projection requires a newer trusted baseline")
    stock_anchors: dict[UUID, PhysicalStockEventAnchor] = {}
    retail_anchors: dict[UUID, RetailSaleEventAnchor] = {}
    refund_anchors: dict[UUID, RetailSaleEventAnchor] = {}
    financial_anchors = {3: retail_anchors, 4: retail_anchors, 6: refund_anchors}
    events = [SyncEventEnvelope.model_validate(row, from_attributes=True) for row in rows]
    for event in events:
        if event.schema_version == 5:
            stock = await repository.get_physical_stock_anchor(
                tenant_id=tenant_id,
                branch_id=branch_id,
                edge_node_id=edge_node_id,
                event_id=event.event_id,
            )
            if stock is None:
                raise ValueError("Physical projection source lacks its independent anchor")
            stock_anchors[event.event_id] = PhysicalStockEventAnchor.model_validate(
                stock, from_attributes=True
            )
            projection = await repository.get_physical_stock_projection(event.event_id)
            if projection is None or projection.payload != event.payload:
                raise ValueError("Physical projection differs from its applied source")
        elif event.schema_version in financial_anchors:
            financial_anchors[event.schema_version][event.event_id] = await _financial_anchor(
                repository, event, edge_node_id
            )
    result = reduce_physical_stock_projection(
        baseline_records=physical_records,
        baseline_configuration=configuration,
        anchor=anchor,
        events=events,
        stock_anchors=stock_anchors,
        retail_anchors=retail_anchors,
        refund_anchors=refund_anchors,
    )
    if (
        result.last_sequence != cursor.last_sequence
        or result.source_checksum != cursor.source_checksum
        or result.projection_checksum != cursor.projection_checksum
    ):
        raise ValueError("Physical projection differs from its common stream cursor")
    return result
