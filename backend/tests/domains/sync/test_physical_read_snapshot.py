"""Opt-in physical snapshots preserve facts without granting a stock writer."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.inventory.unit_ledger_models import (
    UnitStockEntry,
    UnitStockLot,
    UnitStockOpenedPack,
)
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2, stock_availability_v2
from app.domains.sync.credentials import EdgeCredential
from app.domains.sync.physical_read_snapshot import (
    PhysicalCatalogConfigRecord,
    PhysicalCostRecord,
    PhysicalLotRecord,
    PhysicalOpenedPackRecord,
    historical_cost,
    validate_physical_records,
)
from app.domains.sync.physical_read_snapshot_v1 import (
    PhysicalLegacyLotRecord,
    build_legacy_physical_lot,
)
from app.domains.sync.read_snapshot import (
    ChunkRead,
    ManifestRead,
    ReadSnapshotComponent,
    ReadSnapshotItem,
    ReadSnapshotScope,
    ReadSnapshotValidationError,
    build_manifest,
    verify_chunk,
    verify_manifest,
)
from app.domains.sync.read_snapshot_projection import build_indexed_projection, dump_record
from tests.domains.sync.test_read_snapshot import _snapshot
from tests.domains.sync.test_read_snapshot_projection import _catalog, _foundation

TENANT = UUID("10000000-0000-0000-0000-000000000001")
BRANCH = UUID("10000000-0000-0000-0000-000000000002")
NODE, LOT, PACK, SOURCE = (UUID(int=number) for number in (31, 32, 33, 34))
NOW = datetime(2026, 10, 29, tzinfo=UTC)


def physical_config() -> PhysicalCatalogConfigRecord:
    return PhysicalCatalogConfigRecord(
        id=_catalog(1).id,
        tenant_id=TENANT,
        branch_id=BRANCH,
        preparation={
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10,
            "dispense_step": 1,
            "unit_price": "1.25",
        },
    )


def physical_lot() -> PhysicalLotRecord:
    return PhysicalLotRecord(
        id=LOT,
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=_catalog(1).id,
        source_item_id=SOURCE,
        expires_at=date(2027, 10, 29),
        created_at=NOW,
        packaging={
            "unit": "tablet",
            "units_per_pack": 30,
            "units_per_blister": 10,
            "dispense_step": 1,
        },
        initial_closed_packs=3,
        initial_opened_packs=0,
        initial_opened_units=0,
        initial_opened_cost=Decimal("0.00"),
        purchase_cost_per_pack=Decimal("10.00"),
        closed_packs=1,
        is_blocked=False,
        blocking_revision=0,
        opened_packs=(
            PhysicalOpenedPackRecord(
                id=PACK,
                pack_ordinal=2,
                opened_at=NOW,
                state={
                    "id": PACK,
                    "opened_at": NOW,
                    "intact_blisters": (2,),
                    "opened_blisters": ({"number": 1, "remaining_units": 3},),
                    "loose_units": 0,
                },
                remaining_units=13,
            ),
        ),
        cost=PhysicalCostRecord(
            status="known",
            version=1,
            initial_cost=Decimal("30.00"),
            remaining_cost=Decimal("14.33"),
        ),
    )


def physical_components() -> dict[ReadSnapshotComponent, tuple[tuple[ReadSnapshotItem, ...], ...]]:
    return {
        "foundation": ((dump_record(_foundation()),),),
        "catalog": ((dump_record(_catalog(1)),),),
        "inventory": ((dump_record(physical_config()), dump_record(physical_lot())),),
    }


def physical_envelope() -> tuple[EdgeCredential, ManifestRead, list[ChunkRead]]:
    credential = EdgeCredential(kid=UUID(int=35), secret="1" * 64)
    envelope, chunks = build_manifest(
        ReadSnapshotScope(edge_node_id=NODE, tenant_id=TENANT, branch_id=BRANCH, source_revision=2),
        physical_components(),
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=NOW,
        schema_version=2,
    )
    return credential, envelope, chunks


def test_physical_snapshot_requires_explicit_version_support() -> None:
    credential, envelope, chunks = physical_envelope()
    with pytest.raises(ReadSnapshotValidationError):
        verify_manifest(envelope, credential=credential.token)
    manifest = verify_manifest(envelope, credential=credential.token, allow_physical=True)
    assert manifest.schema_version == 2
    assert all(verify_chunk(manifest, chunk).schema_version == 2 for chunk in chunks)


def test_legacy_v1_manifest_golden_is_unchanged() -> None:
    credential, envelope, _chunks = _snapshot()
    assert verify_manifest(envelope, credential=credential.token).schema_version == 1
    assert envelope.manifest_hash == (
        "1afb118ab6074cea9fa55b39b7de9868005d2e99ff5f69c0001c716388c3bb5a"
    )


def test_physical_projection_preserves_typed_lot_and_pack_identity() -> None:
    stored = [
        SimpleNamespace(component=component, payload=payload)
        for component, component_chunks in physical_components().items()
        for payload in component_chunks
    ]
    projection = build_indexed_projection(
        tenant_id=TENANT, branch_id=BRANCH, chunks=stored, schema_version=2
    )
    assert projection.inventory == ()
    assert physical_config() in projection.physical
    assert physical_lot() in projection.physical
    assert physical_lot().opened_packs[0].pack_ordinal == 2


@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_physical_closed_pack_count_is_a_strict_nonnegative_integer(value: object) -> None:
    payload = physical_lot().model_dump() | {"closed_packs": value}
    with pytest.raises(ValidationError):
        PhysicalLotRecord.model_validate(payload)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1.001", "-0.01"])
def test_physical_cost_rejects_invalid_money(value: str) -> None:
    with pytest.raises(ValidationError):
        PhysicalCostRecord(status="known", version=1, initial_cost=value, remaining_cost="0.00")


def test_unknown_cost_never_uses_current_catalog_price() -> None:
    cost = historical_cost(
        initial_closed_packs=3,
        purchase_cost_per_pack=Decimal("10.00"),
        initial_opened_cost=Decimal("0.00"),
        cost_version=1,
        spent=((1, None, None),),
    )
    assert cost.status == "unknown"
    assert cost.initial_cost is None
    assert cost.remaining_cost is None


def test_historical_cost_subtracts_saved_money_without_rounding_units_again() -> None:
    cost = historical_cost(
        initial_closed_packs=3,
        purchase_cost_per_pack=Decimal("10.00"),
        initial_opened_cost=Decimal("0.23"),
        cost_version=1,
        spent=((0, None, None), (1, 1, Decimal("0.34")), (29, 1, Decimal("9.66"))),
    )
    assert cost == PhysicalCostRecord(
        status="known", version=1, initial_cost="30.23", remaining_cost="20.23"
    )


@pytest.mark.parametrize("field", ["tenant_id", "branch_id"])
def test_physical_scope_is_checked_even_for_blocked_lots(field: str) -> None:
    lot = physical_lot().model_copy(update={field: UUID(int=99), "is_blocked": True})
    with pytest.raises(ValueError, match="tenant or branch"):
        validate_physical_records(
            (physical_config(), lot),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_ids={_catalog(1).id},
        )


def test_duplicate_physical_lots_are_rejected() -> None:
    with pytest.raises(ValueError, match="identities must be unique"):
        validate_physical_records(
            (physical_config(), physical_lot(), physical_lot()),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_ids={_catalog(1).id},
        )


def test_each_catalog_requires_an_explicit_configuration_record() -> None:
    with pytest.raises(ValueError, match="exactly one configuration"):
        validate_physical_records(
            (physical_lot(),),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_ids={_catalog(1).id},
        )


@pytest.mark.parametrize("field", ["id", "opened_at"])
def test_opened_pack_identity_matches_historical_state(field: str) -> None:
    payload = physical_lot().opened_packs[0].model_dump()
    payload[field] = UUID(int=99) if field == "id" else datetime(2026, 10, 28, tzinfo=UTC)
    with pytest.raises(ValidationError, match="identity"):
        PhysicalOpenedPackRecord.model_validate(payload)


def test_physical_remainder_must_equal_individual_blister_states() -> None:
    payload = physical_lot().model_dump()
    payload["opened_packs"][0]["remaining_units"] = 14
    with pytest.raises(ValidationError, match="remainder differs"):
        PhysicalLotRecord.model_validate(payload)


def test_exhausted_pack_tombstone_keeps_its_original_ordinal() -> None:
    payload = physical_lot().model_dump()
    tombstone = physical_lot().opened_packs[0].model_dump()
    tombstone.update(id=UUID(int=36), pack_ordinal=1, remaining_units=0)
    tombstone["state"].update(
        id=UUID(int=36), intact_blisters=(), opened_blisters=(), loose_units=0
    )
    payload["opened_packs"] = (*payload["opened_packs"], tombstone)
    restored = PhysicalLotRecord.model_validate(payload)
    assert restored.initial_closed_packs == 3
    assert restored.initial_opened_packs == 0
    assert restored.initial_opened_units == 0
    assert restored.initial_opened_cost == Decimal("0.00")
    assert restored.opened_packs[1].pack_ordinal == 1
    assert restored.opened_packs[1].remaining_units == 0


@pytest.mark.parametrize("status", ["known", "unknown"])
def test_cost_status_requires_matching_evidence(status: str) -> None:
    payload = {
        "status": status,
        "version": 1,
        "initial_cost": None if status == "known" else "10.00",
        "remaining_cost": None,
    }
    with pytest.raises(ValidationError):
        PhysicalCostRecord.model_validate(payload)


def test_opened_pack_identifiers_are_globally_unique_across_lots() -> None:
    first = physical_lot()
    second = first.model_copy(update={"id": UUID(int=37), "source_item_id": UUID(int=38)})
    with pytest.raises(ValueError, match="pack"):
        validate_physical_records(
            (physical_config(), first, second),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_ids={_catalog(1).id},
        )


def test_current_sealed_count_cannot_exceed_original_physical_packs() -> None:
    payload = physical_lot().model_dump() | {"closed_packs": 4}
    with pytest.raises(ValidationError):
        PhysicalLotRecord.model_validate(payload)


def test_opened_ordinal_cannot_also_identify_a_still_sealed_pack() -> None:
    payload = physical_lot().model_dump()
    payload["opened_packs"][0]["pack_ordinal"] = 3
    with pytest.raises(ValidationError):
        PhysicalLotRecord.model_validate(payload)


def test_round_trip_preserves_pack_blister_and_unit_availability() -> None:
    restored = PhysicalLotRecord.model_validate_json(physical_lot().model_dump_json())
    stock = UnitStockBatchV2(
        id=restored.id,
        tenant_id=restored.tenant_id,
        branch_id=restored.branch_id,
        catalog_id=restored.catalog_id,
        expires_at=restored.expires_at,
        created_at=restored.created_at,
        packaging=restored.packaging,
        closed_packs=restored.closed_packs,
        opened_packs=tuple(row.state for row in restored.opened_packs if row.remaining_units),
        is_blocked=restored.is_blocked,
    )
    available = stock_availability_v2(
        batches=(stock,),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=restored.catalog_id,
        today=NOW.date(),
        packaging=restored.packaging,
    )
    assert available.base_units == 43
    assert available.closed_packs == 1
    assert available.intact_blisters == 4
    assert available.lock_opened_pack_ids == (PACK,)


def test_v2_hash_binds_historical_opened_cost_and_physical_remainder() -> None:
    credential, original, chunks = physical_envelope()
    changed = physical_components()
    inventory = list(changed["inventory"][0])
    inventory[-1] = {**inventory[-1], "initial_opened_cost": "0.01"}
    changed["inventory"] = (tuple(inventory),)
    altered, _ = build_manifest(
        ReadSnapshotScope(edge_node_id=NODE, tenant_id=TENANT, branch_id=BRANCH, source_revision=2),
        changed,
        credential_kid=credential.kid,
        credential_digest=credential.digest,
        source_generated_at=NOW,
        snapshot_id=original.manifest.snapshot_id,
        schema_version=2,
    )
    assert original.manifest.snapshot_hash != altered.manifest.snapshot_hash
    original_manifest = verify_manifest(original, credential=credential.token, allow_physical=True)
    inventory_chunk = next(chunk for chunk in chunks if chunk.component == "inventory")
    with pytest.raises(ReadSnapshotValidationError):
        verify_chunk(
            original_manifest,
            inventory_chunk.model_copy(update={"payload": tuple(inventory)}),
        )


@pytest.mark.parametrize("schema_version", [1, 3])
def test_physical_projection_cannot_be_interpreted_as_legacy(schema_version: int) -> None:
    stored = [
        SimpleNamespace(component=component, payload=payload)
        for component, component_chunks in physical_components().items()
        for payload in component_chunks
    ]
    with pytest.raises(ValueError):
        build_indexed_projection(
            tenant_id=TENANT, branch_id=BRANCH, chunks=stored, schema_version=schema_version
        )


def legacy_source() -> tuple[UnitStockLot, tuple[UnitStockOpenedPack, ...], list[UnitStockEntry]]:
    identity = {"tenant_id": TENANT, "branch_id": BRANCH, "catalog_id": _catalog(1).id}
    lot = UnitStockLot(
        **identity,
        id=LOT,
        created_at=NOW,
        state="prepared",
        version=1,
        unit="tablet",
        units_per_pack=30,
        dispense_step=1,
        expires_at=date(2027, 10, 29),
        is_blocked=False,
        closed_packs=1,
        initial_closed_packs=3,
        purchase_cost_per_pack=Decimal("10.00"),
        cost_version=1,
    )
    packs = (
        UnitStockOpenedPack(
            **identity,
            id=UUID(int=40),
            lot_id=LOT,
            pack_ordinal=1,
            opened_at=NOW,
            remaining_units=0,
        ),
        UnitStockOpenedPack(
            **identity,
            id=UUID(int=41),
            lot_id=LOT,
            pack_ordinal=2,
            opened_at=NOW,
            remaining_units=13,
        ),
    )
    entries = [
        UnitStockEntry(
            **identity,
            id=UUID(int=42),
            lot_id=LOT,
            units_delta=-47,
            cost_version=1,
            cost_amount=Decimal("15.67"),
        )
    ]
    return lot, packs, entries


def test_legacy_physical_round_trip_keeps_original_basis_and_tombstones() -> None:
    lot, packs, entries = legacy_source()
    record = build_legacy_physical_lot(lot, packs, entries)
    restored = PhysicalLegacyLotRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.packaging.model_dump() == {
        "version": 1,
        "unit": "tablet",
        "units_per_pack": 30,
        "dispense_step": 1,
    }
    assert restored.initial_closed_packs == 3
    assert [(pack.pack_ordinal, pack.remaining_units) for pack in restored.opened_packs] == [
        (1, 0),
        (2, 13),
    ]
    assert restored.cost_status == "known"
    assert restored.initial_cost == Decimal("30.00")
    assert restored.remaining_cost == Decimal("14.33")
    stock = restored.stock_snapshot()
    assert stock.closed_packs == 1
    assert [(pack.id, pack.remaining_units) for pack in stock.opened_packs] == [(packs[1].id, 13)]


def test_old_legacy_physical_stock_does_not_invent_original_counts_or_cost() -> None:
    lot, packs, entries = legacy_source()
    lot.initial_closed_packs = None
    lot.purchase_cost_per_pack = None
    lot.cost_version = None
    for pack in packs:
        pack.pack_ordinal = None
    record = build_legacy_physical_lot(lot, packs, entries)
    assert record.initial_closed_packs is None
    assert record.purchase_cost_per_pack is None
    assert record.cost_version is None
    assert record.cost_status == "unknown"
    assert record.initial_cost is None and record.remaining_cost is None
    assert all(pack.pack_ordinal is None for pack in record.opened_packs)
    assert record.closed_packs == 1


def test_one_missing_legacy_spend_cost_keeps_whole_cost_unknown() -> None:
    lot, packs, entries = legacy_source()
    entries[0].cost_amount = None
    record = build_legacy_physical_lot(lot, packs, entries)
    assert record.cost_status == "unknown"
    assert record.initial_cost is None and record.remaining_cost is None


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "catalog_id", "lot_id"])
@pytest.mark.parametrize("child", ["pack", "entry"])
def test_legacy_source_children_cannot_escape_lot_scope(field: str, child: str) -> None:
    lot, packs, entries = legacy_source()
    setattr(packs[0] if child == "pack" else entries[0], field, UUID(int=99))
    with pytest.raises(ValueError, match="lot scope"):
        build_legacy_physical_lot(lot, packs, entries)


def test_received_legacy_stock_requires_actual_historical_source() -> None:
    lot, packs, entries = legacy_source()
    lot.state = "received"
    with pytest.raises(ValidationError, match="historical source"):
        build_legacy_physical_lot(lot, packs, entries)
    record = build_legacy_physical_lot(lot, packs, entries, source_item_id=SOURCE)
    assert record.source_item_id == SOURCE


@pytest.mark.parametrize("field", ["cost_version", "purchase_cost_per_pack"])
def test_legacy_cost_rejects_boolean_version_and_float_money(field: str) -> None:
    lot, packs, entries = legacy_source()
    payload = build_legacy_physical_lot(lot, packs, entries).model_dump()
    payload[field] = True if field == "cost_version" else 10.0
    with pytest.raises(ValidationError):
        PhysicalLegacyLotRecord.model_validate(payload)


def initial_opened_lot() -> PhysicalLotRecord:
    payload = physical_lot().model_dump()
    payload.update(
        initial_closed_packs=0,
        initial_opened_packs=1,
        initial_opened_units=13,
        initial_opened_cost=Decimal("0.43"),
        purchase_cost_per_pack=Decimal("99.00"),
        closed_packs=0,
        cost={"status": "unknown", "version": 1, "initial_cost": None, "remaining_cost": None},
    )
    payload["opened_packs"][0]["pack_ordinal"] = 1
    payload["opening_basis"] = (
        {
            "id": PACK,
            "lot_id": LOT,
            "tenant_id": TENANT,
            "branch_id": BRANCH,
            "catalog_id": _catalog(1).id,
            "transition_id": UUID(int=55),
            "pack_ordinal": 1,
            "opened_at": NOW,
            "state": payload["opened_packs"][0]["state"],
            "remaining_units": 13,
            "cost_amount": "0.43",
        },
    )
    return PhysicalLotRecord.model_validate(payload)


def test_initial_opened_only_basis_survives_an_unknown_aggregate_cost() -> None:
    restored = PhysicalLotRecord.model_validate_json(initial_opened_lot().model_dump_json())
    assert restored.initial_closed_packs == 0
    assert restored.initial_opened_packs == 1
    assert restored.initial_opened_units == 13
    assert restored.initial_opened_cost == Decimal("0.43")
    assert restored.purchase_cost_per_pack == Decimal("99.00")
    assert restored.cost.status == "unknown" and restored.cost.version == 1
    assert restored.cost.initial_cost is None and restored.cost.remaining_cost is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", UUID(int=99)),
        ("branch_id", UUID(int=99)),
        ("lot_id", UUID(int=99)),
        ("catalog_id", UUID(int=99)),
        ("remaining_units", 12),
        ("cost_amount", "0.44"),
        ("pack_ordinal", 2),
        ("id", UUID(int=99)),
    ],
)
def test_initial_opened_basis_cannot_change_scope_identity_or_historical_sums(
    field: str, value: object
) -> None:
    payload = initial_opened_lot().model_dump()
    payload["opening_basis"][0][field] = value
    with pytest.raises(ValidationError):
        PhysicalLotRecord.model_validate(payload)
