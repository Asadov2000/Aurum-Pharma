"""Build and install explicitly supported, bounded read-only physical snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from app.domains.catalog.unit_sales import UnitSalePreparation
from app.domains.inventory.unit_stock_v2 import OpenedPackV2, UnitPackagingV2
from app.domains.sync.physical_read_snapshot import (
    PhysicalCatalogConfigRecord,
    PhysicalLotRecord,
    PhysicalOpenedPackRecord,
    PhysicalRecord,
    historical_cost,
    validate_physical_records,
)
from app.domains.sync.physical_read_snapshot_repository import PhysicalSourceFacts
from app.domains.sync.physical_read_snapshot_v1 import build_legacy_physical_lot
from app.domains.sync.read_snapshot import ChunkRead, ManifestRead, verify_chunk, verify_manifest
from app.domains.sync.read_snapshot_projection import (
    IndexedReadProjection,
    build_indexed_projection,
)

if TYPE_CHECKING:
    from app.domains.sync.models import SyncReadSnapshot
    from app.domains.sync.repository import SyncEdgeRepository


def build_physical_source_records(
    facts: PhysicalSourceFacts,
    *,
    tenant_id: UUID,
    branch_id: UUID,
) -> tuple[PhysicalRecord, ...]:
    lot_scope = {lot.id: (lot.tenant_id, lot.branch_id, lot.catalog_id) for lot in facts.lots}
    if len({row.id for row in facts.writeoff_costs}) != len(facts.writeoff_costs):
        raise ValueError("Physical write-off cost source contains duplicate entries")
    if any(
        (row.tenant_id, row.branch_id) != (tenant_id, branch_id)
        or lot_scope.get(row.lot_id) != (row.tenant_id, row.branch_id, row.catalog_id)
        for row in facts.writeoff_costs
    ):
        raise ValueError("Physical write-off cost source escapes its lot scope")
    records: list[PhysicalRecord] = [
        PhysicalCatalogConfigRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            branch_id=branch_id,
            preparation=(
                UnitSalePreparation.model_validate(row.unit_sale_preparation)
                if row.unit_sale_preparation is not None
                else None
            ),
        )
        for row in facts.catalog
    ]
    for lot in facts.lots:
        packs = tuple(
            PhysicalOpenedPackRecord(
                id=pack.id,
                pack_ordinal=pack.pack_ordinal,
                opened_at=pack.opened_at,
                state=OpenedPackV2.model_validate(
                    {**pack.state, "id": pack.id, "opened_at": pack.opened_at}
                ),
                remaining_units=pack.remaining_units,
            )
            for pack in facts.packs
            if pack.lot_id == lot.id
        )
        records.append(
            PhysicalLotRecord(
                id=lot.id,
                tenant_id=lot.tenant_id,
                branch_id=lot.branch_id,
                catalog_id=lot.catalog_id,
                source_item_id=lot.source_item_id,
                expires_at=lot.expires_at,
                created_at=lot.created_at,
                packaging=UnitPackagingV2.model_validate(lot.packaging),
                initial_closed_packs=lot.initial_closed_packs,
                initial_opened_packs=lot.initial_opened_packs,
                initial_opened_units=lot.initial_opened_units,
                initial_opened_cost=lot.initial_opened_cost,
                purchase_cost_per_pack=lot.purchase_cost_per_pack,
                closed_packs=lot.closed_packs,
                is_blocked=lot.is_blocked,
                blocking_revision=lot.blocking_revision,
                opened_packs=packs,
                opening_basis=tuple(p for p in facts.opening_basis if p.lot_id == lot.id),
                cost=historical_cost(
                    initial_closed_packs=lot.initial_closed_packs,
                    purchase_cost_per_pack=lot.purchase_cost_per_pack,
                    initial_opened_cost=lot.initial_opened_cost,
                    cost_version=lot.cost_version,
                    spent=[
                        (m.units_taken, m.cost_version, m.cost_amount)
                        for m in facts.movements
                        if m.lot_id == lot.id
                    ]
                    + [
                        (entry.units_taken, entry.cost_version, entry.cost_amount)
                        for entry in facts.writeoff_costs
                        if entry.lot_id == lot.id
                    ],
                ),
            )
        )
    for legacy_lot in facts.legacy_lots:
        records.append(
            build_legacy_physical_lot(
                legacy_lot,
                [p for p in facts.legacy_packs if p.lot_id == legacy_lot.id],
                [e for e in facts.legacy_entries if e.lot_id == legacy_lot.id],
                source_item_id=facts.legacy_sources.get(legacy_lot.id),
            )
        )
    return validate_physical_records(
        records,
        tenant_id=tenant_id,
        branch_id=branch_id,
        catalog_ids={row.id for row in facts.catalog},
    )


async def install_physical_read_snapshot(
    repo: SyncEdgeRepository,
    *,
    envelope: ManifestRead,
    chunks: Sequence[ChunkRead],
    credential: str,
) -> None:
    """Explicit opt-in entry; old workers continue to reject this profile."""
    manifest = verify_manifest(envelope, credential=credential, allow_physical=True)
    if manifest.schema_version != 2:
        raise ValueError("Physical restore requires version two")
    expected = {(c.component, d.chunk_index) for c in manifest.components for d in c.chunks}
    actual = [(c.component, c.chunk_index) for c in chunks]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError("Physical snapshot chunk set is incomplete or duplicated")
    for chunk in chunks:
        verify_chunk(manifest, chunk)
    projection = build_indexed_projection(
        tenant_id=manifest.tenant_id, branch_id=manifest.branch_id, chunks=chunks, schema_version=2
    )
    async with repo.session.begin_nested():
        await repo.lock_read_snapshot_install(
            tenant_id=manifest.tenant_id,
            branch_id=manifest.branch_id,
            edge_node_id=manifest.edge_node_id,
        )
        active = await repo.get_active_read_projection(
            tenant_id=manifest.tenant_id,
            branch_id=manifest.branch_id,
            credential_kid=manifest.credential_kid,
        )
        if active is not None and active[0].snapshot_id == manifest.snapshot_id:
            stored_envelope, stored_projection = await _verified_stored_projection(
                repo, snapshot=active[0], credential=credential
            )
            if (
                stored_envelope != envelope
                or stored_projection != projection
                or active[1].physical_payload
                != [row.model_dump(mode="json") for row in projection.physical]
            ):
                raise ValueError("Repeated physical snapshot differs from stored history")
            return
        await repo.install_read_snapshot(
            envelope=envelope, chunks=list(chunks), projection=projection
        )


async def _verified_stored_projection(
    repo: SyncEdgeRepository,
    *,
    snapshot: SyncReadSnapshot,
    credential: str,
) -> tuple[ManifestRead, IndexedReadProjection]:
    from app.core.errors import ServiceUnavailableError
    from app.domains.sync.read_snapshot import (
        REQUIRED_COMPONENTS,
        ChunkDescriptor,
        ComponentDescriptor,
        Manifest,
    )

    components, chunks = await repo.get_read_snapshot_verification_bundle(
        snapshot_id=snapshot.snapshot_id,
        tenant_id=snapshot.tenant_id,
        branch_id=snapshot.branch_id,
        edge_node_id=snapshot.node_id,
    )
    typed_chunks = [
        ChunkRead.model_validate(
            {
                "schema_version": chunk.schema_version,
                "snapshot_id": chunk.snapshot_id,
                "edge_node_id": chunk.node_id,
                "tenant_id": chunk.tenant_id,
                "branch_id": chunk.branch_id,
                "source_revision": chunk.source_revision,
                "component": chunk.component,
                "chunk_index": chunk.chunk_index,
                "item_count": chunk.item_count,
                "payload_hash": chunk.payload_hash,
                "payload": chunk.payload.get("items"),
            }
        )
        for chunk in chunks
    ]
    by_name = {row.component: row for row in components}
    if len(by_name) != len(components) or set(by_name) != set(REQUIRED_COMPONENTS):
        raise ServiceUnavailableError("Physical snapshot component coverage is invalid")
    descriptors = tuple(
        ComponentDescriptor(
            schema_version=by_name[name].schema_version,
            component=name,
            item_count=by_name[name].item_count,
            chunk_count=by_name[name].chunk_count,
            component_hash=by_name[name].component_hash,
            chunks=tuple(
                ChunkDescriptor(
                    schema_version=c.schema_version,
                    chunk_index=c.chunk_index,
                    item_count=c.item_count,
                    payload_hash=c.payload_hash,
                )
                for c in sorted(typed_chunks, key=lambda c: c.chunk_index)
                if c.component == name
            ),
        )
        for name in REQUIRED_COMPONENTS
    )
    envelope = ManifestRead(
        manifest=Manifest(
            schema_version=snapshot.schema_version,
            profile="physical_read_only_v2",
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
    manifest = verify_manifest(envelope, credential=credential, allow_physical=True)
    actual = [(c.component, c.chunk_index) for c in typed_chunks]
    expected = {(c.component, d.chunk_index) for c in manifest.components for d in c.chunks}
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ServiceUnavailableError("Physical snapshot chunk coverage is invalid")
    for chunk in typed_chunks:
        verify_chunk(manifest, chunk)
    projection = build_indexed_projection(
        tenant_id=snapshot.tenant_id,
        branch_id=snapshot.branch_id,
        chunks=typed_chunks,
        schema_version=2,
    )
    return envelope, projection


async def read_physical_snapshot(
    repo: SyncEdgeRepository,
    *,
    tenant_id: UUID,
    branch_id: UUID,
    actor_id: UUID,
    credential: str,
) -> tuple[PhysicalRecord, ...]:
    """Private historical facts require current branch read AND cost permission."""
    from sqlalchemy import text

    from app.core.errors import PermissionDeniedError, ServiceUnavailableError
    from app.domains.sync.credentials import parse_edge_credential

    allowed = await repo.session.scalar(
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
        raise PermissionDeniedError("Physical snapshot historical cost permission denied")
    parsed = parse_edge_credential(credential)
    active = await repo.get_active_read_projection(
        tenant_id=tenant_id, branch_id=branch_id, credential_kid=parsed.kid
    )
    if active is None or active[0].schema_version != 2:
        raise ServiceUnavailableError("Physical snapshot is not installed")
    snapshot, header, _ = active
    _, projection = await _verified_stored_projection(
        repo, snapshot=snapshot, credential=credential
    )
    if (
        header.projection_version != 2
        or header.physical_count != len(projection.physical)
        or header.physical_payload != [row.model_dump(mode="json") for row in projection.physical]
    ):
        raise ServiceUnavailableError("Physical snapshot projection differs from signed history")
    return projection.physical
