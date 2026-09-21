"""Local read-only API over the atomically activated Edge snapshot."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
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
from app.domains.sync.models import SyncReadSnapshot
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


type SourceVerifier = Callable[[SyncReadSnapshot, str], Awaitable[None]]

_VERIFIED_SOURCE_CACHE_LIMIT = 8
_verified_source_cache: OrderedDict[tuple[UUID, str], None] = OrderedDict()


class LocalReadModel:
    def __init__(
        self,
        repo: SyncEdgeRepository,
        *,
        source_verifier: SourceVerifier | None = None,
    ) -> None:
        self.repo = repo
        self._source_verifier = source_verifier

    async def _verify_stored_source(self, snapshot: SyncReadSnapshot, credential: str) -> None:
        cache_key = (snapshot.snapshot_id, snapshot.signature)
        if cache_key in _verified_source_cache:
            _verified_source_cache.move_to_end(cache_key)
            return
        if self._source_verifier is not None:
            await self._source_verifier(snapshot, credential)
        else:
            components, chunks = await self.repo.get_read_snapshot_verification_bundle(
                snapshot_id=snapshot.snapshot_id,
                tenant_id=snapshot.tenant_id,
                branch_id=snapshot.branch_id,
                edge_node_id=snapshot.node_id,
            )
            component_by_name = {row.component: row for row in components}
            try:
                descriptors = tuple(
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
                        components=descriptors,
                        snapshot_hash=snapshot.snapshot_hash,
                    ),
                    manifest_hash=snapshot.manifest_hash,
                    signature_algorithm=snapshot.signature_algorithm,
                    signature=snapshot.signature,
                )
                manifest = verify_manifest(envelope, credential=credential)
                for chunk in chunks:
                    raw_items = chunk.payload.get("items")
                    if not isinstance(raw_items, list) or any(
                        not isinstance(item, dict) for item in raw_items
                    ):
                        raise ReadSnapshotValidationError("Chunk payload is invalid")
                    verify_chunk(
                        manifest,
                        ChunkRead.model_validate(
                            {
                                "schema_version": chunk.schema_version,
                                "snapshot_id": chunk.snapshot_id,
                                "edge_node_id": chunk.node_id,
                                "tenant_id": chunk.tenant_id,
                                "branch_id": chunk.branch_id,
                                "source_revision": chunk.source_revision,
                                "component": cast(ReadSnapshotComponent, chunk.component),
                                "chunk_index": chunk.chunk_index,
                                "item_count": chunk.item_count,
                                "payload_hash": chunk.payload_hash,
                                "payload": raw_items,
                            }
                        ),
                    )
            except (KeyError, PydanticValidationError, ReadSnapshotValidationError) as exc:
                raise ServiceUnavailableError("Local Edge snapshot is corrupted") from exc
        _verified_source_cache[cache_key] = None
        _verified_source_cache.move_to_end(cache_key)
        while len(_verified_source_cache) > _VERIFIED_SOURCE_CACHE_LIMIT:
            _verified_source_cache.popitem(last=False)

    async def _load_projection(
        self, *, tenant_id: UUID, branch_id: UUID
    ) -> tuple[SnapshotMetadata, FoundationRecord, UUID]:
        credential = get_settings().EDGE_SYNC_CREDENTIAL
        if credential is None:
            raise ServiceUnavailableError("Edge runtime credential is unavailable")
        credential_value = credential.get_secret_value()
        try:
            parsed = parse_edge_credential(credential_value)
        except ValueError as exc:
            raise ServiceUnavailableError("Edge runtime credential is invalid") from exc
        stored = await self.repo.get_active_read_projection(
            tenant_id=tenant_id,
            branch_id=branch_id,
            credential_kid=parsed.kid,
        )
        if stored is None:
            raise ServiceUnavailableError("Local Edge snapshot is not installed")
        snapshot, projection, confirmed_at = stored
        await self._verify_stored_source(snapshot, credential_value)
        try:
            foundation = FoundationRecord.model_validate(projection.foundation_payload)
        except PydanticValidationError as exc:
            raise ServiceUnavailableError("Local Edge snapshot is corrupted") from exc
        if foundation.tenant.id != tenant_id or foundation.branch.id != branch_id:
            raise ServiceUnavailableError("Local Edge snapshot is corrupted")
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
        return metadata, foundation, snapshot.snapshot_id

    async def foundation(self, *, tenant_id: UUID, branch_id: UUID) -> FoundationReadModel:
        metadata, foundation, _ = await self._load_projection(
            tenant_id=tenant_id, branch_id=branch_id
        )
        return FoundationReadModel(metadata=metadata, foundation=foundation)

    async def catalog(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        query: str | None,
        page: int,
        page_size: int,
    ) -> CatalogReadModel:
        metadata, _, snapshot_id = await self._load_projection(
            tenant_id=tenant_id, branch_id=branch_id
        )
        normalized_query = (query or "").strip().casefold()
        start = (page - 1) * page_size
        rows, total = await self.repo.list_read_catalog_projection(
            snapshot_id=snapshot_id,
            query=normalized_query or None,
            offset=start,
            limit=page_size,
        )
        try:
            products = [CatalogProductRecord.model_validate(row.payload) for row in rows]
        except PydanticValidationError as exc:
            raise ServiceUnavailableError("Local Edge projection is corrupted") from exc
        if any(product.tenant_id != tenant_id for product in products):
            raise ServiceUnavailableError("Local Edge projection is corrupted")
        return CatalogReadModel(
            metadata=metadata,
            items=[
                CatalogItemRead.model_validate(row.model_dump(exclude={"record_type", "barcodes"}))
                for row in products
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
        metadata, _, snapshot_id = await self._load_projection(
            tenant_id=tenant_id, branch_id=branch_id
        )
        stored = await self.repo.get_read_catalog_projection(
            snapshot_id=snapshot_id,
            item_id=item_id,
            barcode=barcode,
        )
        if stored is None:
            raise NotFoundError("Catalog item not found")
        row, barcode_rows = stored
        try:
            product = CatalogProductRecord.model_validate(row.payload)
            if product.tenant_id != tenant_id:
                raise ServiceUnavailableError("Local Edge projection is corrupted")
            item = CatalogItemWithBarcodes.model_validate(
                {
                    **product.model_dump(exclude={"record_type", "barcodes"}),
                    "barcodes": [
                        {"id": value.barcode_id, "code": value.code, "code_type": value.code_type}
                        for value in barcode_rows
                    ],
                }
            )
        except PydanticValidationError as exc:
            raise ServiceUnavailableError("Local Edge projection is corrupted") from exc
        return CatalogDetailsReadModel(metadata=metadata, item=item)

    async def suppliers(
        self, *, tenant_id: UUID, branch_id: UUID, query: str | None, page: int, page_size: int
    ) -> SupplierReadModel:
        metadata, _, snapshot_id = await self._load_projection(
            tenant_id=tenant_id, branch_id=branch_id
        )
        normalized_query = (query or "").strip().casefold()
        start = (page - 1) * page_size
        stored, total = await self.repo.list_read_supplier_projection(
            snapshot_id=snapshot_id,
            query=normalized_query or None,
            offset=start,
            limit=page_size,
        )
        try:
            rows = [SupplierRecord.model_validate(row.payload) for row in stored]
        except PydanticValidationError as exc:
            raise ServiceUnavailableError("Local Edge projection is corrupted") from exc
        if any(row.tenant_id != tenant_id for row in rows):
            raise ServiceUnavailableError("Local Edge projection is corrupted")
        return SupplierReadModel(
            metadata=metadata,
            items=[
                SupplierRead.model_validate(row.model_dump(exclude={"record_type"})) for row in rows
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
        metadata, foundation, snapshot_id = await self._load_projection(
            tenant_id=tenant_id, branch_id=branch_id
        )
        raw_timezone = foundation.settings.report_timezone
        raw_thresholds = foundation.settings.expiry_thresholds
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

        start = (page - 1) * page_size
        stored, total, stored_summary = await self.repo.list_read_inventory_projection(
            snapshot_id=snapshot_id,
            show_empty=show_empty,
            offset=start,
            limit=page_size,
            today=boundaries.today,
            orange_until=boundaries.orange_until,
        )
        try:
            rows = [InventoryBatchRecord.model_validate(row.payload) for row in stored]
        except PydanticValidationError as exc:
            raise ServiceUnavailableError("Local Edge projection is corrupted") from exc
        if any(row.tenant_id != tenant_id or row.branch_id != branch_id for row in rows):
            raise ServiceUnavailableError("Local Edge projection is corrupted")
        items = [
            BatchWithExpiry.model_validate(
                {
                    **row.model_dump(exclude={"record_type"}),
                    "purchase_price": row.purchase_price if can_view_costs else None,
                    "expiry_status": status_for(row.expires_at),
                    "days_to_expiry": (row.expires_at - boundaries.today).days,
                }
            )
            for row in rows
        ]
        return InventoryReadModel(
            metadata=metadata,
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            summary=BatchSummary(
                total_qty=stored_summary.total_qty,
                purchase_value=stored_summary.purchase_value if can_view_costs else None,
                sale_value=stored_summary.sale_value,
                attention_count=stored_summary.attention_count,
                expired_count=stored_summary.expired_count,
                blocked_count=stored_summary.blocked_count,
            ),
        )
