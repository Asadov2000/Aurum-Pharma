"""Local read-only API over the atomically activated Edge snapshot."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from app.core.config import get_settings
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.domains.catalog.schemas import CatalogItemRead, CatalogItemWithBarcodes
from app.domains.inventory.expiry import ExpiryStatus, build_expiry_boundaries
from app.domains.inventory.schemas import BatchSummary, BatchWithExpiry
from app.domains.suppliers.schemas import SupplierRead
from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.read_snapshot import (
    REQUIRED_COMPONENTS,
    ChunkDescriptor,
    ChunkRead,
    ComponentDescriptor,
    Manifest,
    ManifestRead,
    ReadSnapshotComponent,
    ReadSnapshotValidationError,
    verify_chunk,
    verify_manifest,
)
from app.domains.sync.read_snapshot_projection import (
    CatalogProductRecord,
    FoundationRecord,
    InventoryBatchRecord,
    SupplierRecord,
    parse_component_items,
    validate_projection_scope,
)
from app.domains.sync.repository import SyncEdgeRepository


class _ReadModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SnapshotMetadata(_ReadModelResponse):
    snapshot_id: UUID
    source_revision: int
    source_generated_at: datetime
    last_confirmed_at: datetime
    age_seconds: int
    is_stale: bool
    source: str = "local_edge"


class FoundationReadModel(_ReadModelResponse):
    metadata: SnapshotMetadata
    foundation: FoundationRecord


class CatalogReadModel(_ReadModelResponse):
    metadata: SnapshotMetadata
    items: list[CatalogItemRead]
    total: int
    page: int
    page_size: int


class CatalogDetailsReadModel(_ReadModelResponse):
    metadata: SnapshotMetadata
    item: CatalogItemWithBarcodes


class SupplierReadModel(_ReadModelResponse):
    metadata: SnapshotMetadata
    items: list[SupplierRead]
    total: int
    page: int
    page_size: int


class InventoryReadModel(_ReadModelResponse):
    metadata: SnapshotMetadata
    items: list[BatchWithExpiry]
    total: int
    page: int
    page_size: int
    summary: BatchSummary


class LocalReadModel:
    def __init__(self, repo: SyncEdgeRepository) -> None:
        self.repo = repo

    async def load(
        self, *, tenant_id: UUID, branch_id: UUID
    ) -> tuple[SnapshotMetadata, dict[ReadSnapshotComponent, tuple[BaseModel, ...]]]:
        credential = get_settings().EDGE_SYNC_CREDENTIAL
        if credential is None:
            raise ServiceUnavailableError("Edge runtime credential is unavailable")
        try:
            parsed = parse_edge_credential(credential.get_secret_value())
        except ValueError as exc:
            raise ServiceUnavailableError("Edge runtime credential is invalid") from exc
        stored = await self.repo.get_active_read_snapshot(
            tenant_id=tenant_id,
            branch_id=branch_id,
            credential_kid=parsed.kid,
        )
        if stored is None:
            raise ServiceUnavailableError("Local Edge snapshot is not installed")
        snapshot, component_rows, chunks, confirmed_at = stored
        component_by_name = {row.component: row for row in component_rows}
        try:
            components = tuple(
                ComponentDescriptor(
                    schema_version=component_by_name[name].schema_version,
                    component=name,
                    item_count=component_by_name[name].item_count,
                    chunk_count=component_by_name[name].chunk_count,
                    chunks=tuple(
                        ChunkDescriptor(
                            schema_version=chunk.schema_version,
                            chunk_index=chunk.chunk_index,
                            item_count=chunk.item_count,
                            payload_hash=chunk.payload_hash,
                        )
                        for chunk in chunks
                        if chunk.component == name
                    ),
                    component_hash=component_by_name[name].component_hash,
                )
                for name in REQUIRED_COMPONENTS
            )
            envelope = ManifestRead(
                manifest=Manifest(
                    schema_version=snapshot.schema_version,
                    snapshot_id=snapshot.snapshot_id,
                    edge_node_id=snapshot.node_id,
                    tenant_id=snapshot.tenant_id,
                    branch_id=snapshot.branch_id,
                    source_revision=snapshot.source_revision,
                    source_generated_at=snapshot.source_generated_at,
                    credential_kid=snapshot.credential_kid,
                    components=components,
                    snapshot_hash=snapshot.snapshot_hash,
                ),
                manifest_hash=snapshot.manifest_hash,
                signature_algorithm=snapshot.signature_algorithm,
                signature=snapshot.signature,
            )
            manifest = verify_manifest(envelope, credential=credential.get_secret_value())
        except (KeyError, PydanticValidationError, ReadSnapshotValidationError) as exc:
            raise ServiceUnavailableError("Local Edge snapshot is corrupted") from exc
        items_by_component: dict[ReadSnapshotComponent, list[dict[str, object]]] = {
            "foundation": [],
            "catalog": [],
            "inventory": [],
        }
        for chunk in chunks:
            if chunk.component not in items_by_component:
                raise ServiceUnavailableError("Local Edge snapshot is corrupted")
            raw_items = chunk.payload.get("items")
            if not isinstance(raw_items, list) or any(
                not isinstance(item, dict) for item in raw_items
            ):
                raise ServiceUnavailableError("Local Edge snapshot is corrupted")
            component = cast(ReadSnapshotComponent, chunk.component)
            try:
                verified_chunk = ChunkRead.model_validate(
                    {
                        "schema_version": chunk.schema_version,
                        "snapshot_id": chunk.snapshot_id,
                        "edge_node_id": chunk.node_id,
                        "tenant_id": chunk.tenant_id,
                        "branch_id": chunk.branch_id,
                        "source_revision": chunk.source_revision,
                        "component": component,
                        "chunk_index": chunk.chunk_index,
                        "item_count": chunk.item_count,
                        "payload_hash": chunk.payload_hash,
                        "payload": raw_items,
                    }
                )
                verify_chunk(manifest, verified_chunk)
            except (PydanticValidationError, ReadSnapshotValidationError) as exc:
                raise ServiceUnavailableError("Local Edge snapshot is corrupted") from exc
            items_by_component[component].extend(raw_items)
        try:
            parsed_components = {
                component: parse_component_items(component, payloads)
                for component, payloads in items_by_component.items()
            }
            validate_projection_scope(
                tenant_id=tenant_id,
                branch_id=branch_id,
                components=parsed_components,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ServiceUnavailableError("Local Edge snapshot is corrupted") from exc
        now = datetime.now(UTC)
        source_age_seconds = int((now - snapshot.source_generated_at).total_seconds())
        age_seconds = int((now - confirmed_at).total_seconds())
        minimum_age = min(source_age_seconds, age_seconds)
        if minimum_age < -get_settings().EDGE_SYNC_MAX_CLOCK_SKEW_SECONDS:
            raise ServiceUnavailableError("Local Edge snapshot clock is invalid")
        age_seconds = max(0, age_seconds)
        metadata = SnapshotMetadata(
            snapshot_id=snapshot.snapshot_id,
            source_revision=snapshot.source_revision,
            source_generated_at=snapshot.source_generated_at,
            last_confirmed_at=confirmed_at,
            age_seconds=age_seconds,
            is_stale=age_seconds > get_settings().EDGE_READ_SNAPSHOT_STALE_SECONDS,
        )
        return metadata, parsed_components

    async def foundation(self, *, tenant_id: UUID, branch_id: UUID) -> FoundationReadModel:
        metadata, components = await self.load(tenant_id=tenant_id, branch_id=branch_id)
        rows = components["foundation"]
        if len(rows) != 1 or not isinstance(rows[0], FoundationRecord):
            raise ServiceUnavailableError("Local Edge foundation snapshot is incomplete")
        return FoundationReadModel(metadata=metadata, foundation=rows[0])

    async def catalog(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        query: str | None,
        page: int,
        page_size: int,
    ) -> CatalogReadModel:
        metadata, components = await self.load(tenant_id=tenant_id, branch_id=branch_id)
        products = [row for row in components["catalog"] if isinstance(row, CatalogProductRecord)]
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            products = [
                row
                for row in products
                if normalized_query
                in " ".join(
                    value for value in (row.brand_name, row.inn, row.manufacturer) if value
                ).casefold()
                or any(normalized_query in barcode.code.casefold() for barcode in row.barcodes)
            ]
        total = len(products)
        start = (page - 1) * page_size
        return CatalogReadModel(
            metadata=metadata,
            items=[
                CatalogItemRead.model_validate(row.model_dump(exclude={"record_type", "barcodes"}))
                for row in products[start : start + page_size]
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def catalog_details(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        item_id: UUID | None = None,
        barcode: str | None = None,
    ) -> CatalogDetailsReadModel:
        metadata, components = await self.load(tenant_id=tenant_id, branch_id=branch_id)
        for row in components["catalog"]:
            if not isinstance(row, CatalogProductRecord):
                continue
            matches_barcode = barcode is not None and any(
                item.code == barcode for item in row.barcodes
            )
            if row.id == item_id or matches_barcode:
                item = CatalogItemWithBarcodes.model_validate(
                    {**row.model_dump(exclude={"record_type"}), "barcodes": row.barcodes}
                )
                return CatalogDetailsReadModel(metadata=metadata, item=item)
        raise NotFoundError("Catalog item not found")

    async def suppliers(
        self, *, tenant_id: UUID, branch_id: UUID, query: str | None, page: int, page_size: int
    ) -> SupplierReadModel:
        metadata, components = await self.load(tenant_id=tenant_id, branch_id=branch_id)
        rows = [row for row in components["catalog"] if isinstance(row, SupplierRecord)]
        normalized_query = (query or "").strip().casefold()
        if normalized_query:
            rows = [
                row
                for row in rows
                if normalized_query
                in " ".join(
                    filter(None, (row.name, row.legal_name, row.inn_or_tin, row.contact_person))
                ).casefold()
            ]
        total = len(rows)
        start = (page - 1) * page_size
        return SupplierReadModel(
            metadata=metadata,
            items=[
                SupplierRead.model_validate(row.model_dump(exclude={"record_type"}))
                for row in rows[start : start + page_size]
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def inventory(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        can_view_costs: bool,
        show_empty: bool,
        page: int,
        page_size: int,
    ) -> InventoryReadModel:
        metadata, components = await self.load(tenant_id=tenant_id, branch_id=branch_id)
        rows = [row for row in components["inventory"] if isinstance(row, InventoryBatchRecord)]
        if not show_empty:
            rows = [row for row in rows if row.qty_remaining > 0]
        foundation_rows = components["foundation"]
        if len(foundation_rows) != 1 or not isinstance(foundation_rows[0], FoundationRecord):
            raise ServiceUnavailableError("Local Edge foundation snapshot is incomplete")
        raw_timezone = foundation_rows[0].settings.report_timezone
        raw_thresholds = foundation_rows[0].settings.expiry_thresholds
        if not isinstance(raw_timezone, str):
            raise ServiceUnavailableError("Local Edge timezone is invalid")
        try:
            today = datetime.now(ZoneInfo(raw_timezone)).date()
        except ZoneInfoNotFoundError as exc:
            raise ServiceUnavailableError("Local Edge timezone is invalid") from exc
        boundaries = build_expiry_boundaries(today, raw_thresholds)

        def status_for(expires_at: date) -> ExpiryStatus:
            if expires_at < boundaries.today:
                return "expired"
            if expires_at <= boundaries.red_until:
                return "red"
            if expires_at <= boundaries.orange_until:
                return "orange"
            if expires_at <= boundaries.yellow_until:
                return "yellow"
            return "normal"

        total_qty = sum((row.qty_remaining for row in rows), Decimal("0"))
        purchase_value = sum((row.qty_remaining * row.purchase_price for row in rows), Decimal("0"))
        sale_value = sum((row.qty_remaining * row.sale_price for row in rows), Decimal("0"))
        statuses = [status_for(row.expires_at) for row in rows]
        total = len(rows)
        start = (page - 1) * page_size
        items = [
            BatchWithExpiry.model_validate(
                {
                    **row.model_dump(exclude={"record_type"}),
                    "purchase_price": row.purchase_price if can_view_costs else None,
                    "expiry_status": status_for(row.expires_at),
                    "days_to_expiry": (row.expires_at - boundaries.today).days,
                }
            )
            for row in rows[start : start + page_size]
        ]
        return InventoryReadModel(
            metadata=metadata,
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            summary=BatchSummary(
                total_qty=total_qty,
                purchase_value=purchase_value if can_view_costs else None,
                sale_value=sale_value,
                attention_count=sum(status in {"expired", "red", "orange"} for status in statuses),
                expired_count=statuses.count("expired"),
                blocked_count=sum(row.is_blocked for row in rows),
            ),
        )
