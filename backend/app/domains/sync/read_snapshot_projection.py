"""Typed projection used by Cloud snapshots and the local Edge read model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domains.sync.read_snapshot import ReadSnapshotComponent, ReadSnapshotItem


class _SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TenantSnapshot(_SnapshotRecord):
    id: UUID
    name: str
    legal_name: str | None
    inn_or_tin: str | None
    registration_number: str | None
    legal_address: str | None
    status: str
    drug_catalog_mode: str
    updated_at: datetime


class TenantSettingsSnapshot(_SnapshotRecord):
    expiry_thresholds: dict[str, int]
    expired_sale_mode: str
    refund_reason_mode: str
    pos_payment_methods: tuple[str, ...]
    pos_mixed_payment_enabled: bool
    draft_sale_lifetime_min: int
    report_timezone: str
    prescription_warning_text: str
    version: int
    updated_at: datetime


class BranchSnapshot(_SnapshotRecord):
    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    branch_type: str
    license_number: str | None
    license_expires_at: date | None
    working_hours: dict[str, object] | None
    receipt_header: dict[str, object] | None
    is_active: bool
    updated_at: datetime


class RegisterSnapshot(_SnapshotRecord):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    name: str
    printer_type: str | None
    printer_config: dict[str, object] | None
    card_terminal_id: str | None
    qr_terminal_id: str | None
    is_active: bool
    updated_at: datetime


class BarcodeSnapshot(_SnapshotRecord):
    id: UUID
    code: str
    code_type: str


class FoundationRecord(_SnapshotRecord):
    record_type: Literal["foundation"] = "foundation"
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant: TenantSnapshot
    settings: TenantSettingsSnapshot
    branch: BranchSnapshot
    registers: tuple[RegisterSnapshot, ...]


class CatalogProductRecord(_SnapshotRecord):
    record_type: Literal["catalog_product"] = "catalog_product"
    id: UUID
    tenant_id: UUID
    brand_name: str
    inn: str | None
    manufacturer: str | None
    form: str | None
    dosage: str | None
    pack_size: str | None
    atx_code: str | None
    dispensing_type: str
    storage_type: str
    category: str | None
    base_price: Decimal | None
    currency: str
    image_version: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    barcodes: tuple[BarcodeSnapshot, ...]


class SupplierRecord(_SnapshotRecord):
    record_type: Literal["supplier"] = "supplier"
    id: UUID
    tenant_id: UUID
    name: str
    legal_name: str | None
    inn_or_tin: str | None
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class InventoryBatchRecord(_SnapshotRecord):
    record_type: Literal["inventory_batch"] = "inventory_batch"
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    batch_number: str | None
    manufactured_at: date | None
    expires_at: date
    purchase_price: Decimal
    sale_price: Decimal
    currency: str
    qty_initial: Decimal
    qty_remaining: Decimal
    is_blocked: bool
    block_reason: str | None
    blocked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None


CatalogRecord = Annotated[CatalogProductRecord | SupplierRecord, Field(discriminator="record_type")]
CATALOG_RECORD_ADAPTER: TypeAdapter[CatalogRecord] = TypeAdapter(CatalogRecord)
INVENTORY_RECORD_ADAPTER: TypeAdapter[InventoryBatchRecord] = TypeAdapter(InventoryBatchRecord)
FOUNDATION_RECORD_ADAPTER: TypeAdapter[FoundationRecord] = TypeAdapter(FoundationRecord)


def dump_record(record: BaseModel) -> ReadSnapshotItem:
    """Convert a strict record to the JSON-only transport contract."""

    return cast(ReadSnapshotItem, record.model_dump(mode="json"))


def chunk_component(
    items: Sequence[ReadSnapshotItem], *, chunk_size: int
) -> tuple[tuple[ReadSnapshotItem, ...], ...]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if not items:
        return ((),)
    return tuple(
        tuple(items[index : index + chunk_size]) for index in range(0, len(items), chunk_size)
    )


def build_component_chunks(
    *,
    foundation: FoundationRecord,
    catalog: Sequence[CatalogProductRecord],
    suppliers: Sequence[SupplierRecord],
    inventory: Sequence[InventoryBatchRecord],
    chunk_size: int,
) -> dict[ReadSnapshotComponent, tuple[tuple[ReadSnapshotItem, ...], ...]]:
    """Return deterministic, validated component chunks."""

    catalog_records: list[CatalogProductRecord | SupplierRecord] = [*catalog, *suppliers]
    catalog_records.sort(key=lambda item: (item.record_type, str(item.id)))
    inventory_records = sorted(inventory, key=lambda item: str(item.id))
    return {
        "foundation": chunk_component((dump_record(foundation),), chunk_size=chunk_size),
        "catalog": chunk_component(
            tuple(dump_record(item) for item in catalog_records), chunk_size=chunk_size
        ),
        "inventory": chunk_component(
            tuple(dump_record(item) for item in inventory_records), chunk_size=chunk_size
        ),
    }


def parse_component_items(
    component: ReadSnapshotComponent,
    payloads: Sequence[Mapping[str, object]],
) -> tuple[BaseModel, ...]:
    """Validate stored JSON before it reaches a local API response."""

    if component == "foundation":
        return tuple(
            FOUNDATION_RECORD_ADAPTER.validate_python(dict(payload)) for payload in payloads
        )
    if component == "catalog":
        return tuple(CATALOG_RECORD_ADAPTER.validate_python(dict(payload)) for payload in payloads)
    return tuple(INVENTORY_RECORD_ADAPTER.validate_python(dict(payload)) for payload in payloads)


def validate_projection_scope(
    *,
    tenant_id: UUID,
    branch_id: UUID,
    components: Mapping[ReadSnapshotComponent, Sequence[BaseModel]],
) -> None:
    """Reject a correctly signed projection whose embedded records escape its scope."""

    foundation = components.get("foundation", ())
    if len(foundation) != 1 or not isinstance(foundation[0], FoundationRecord):
        raise ValueError("Foundation projection must contain exactly one record")
    root = foundation[0]
    if root.tenant.id != tenant_id or root.branch.tenant_id != tenant_id:
        raise ValueError("Foundation tenant does not match snapshot scope")
    if root.branch.id != branch_id:
        raise ValueError("Foundation branch does not match snapshot scope")
    if any(
        register.tenant_id != tenant_id or register.branch_id != branch_id
        for register in root.registers
    ):
        raise ValueError("Register does not match snapshot scope")
    for record in components.get("catalog", ()):
        if not isinstance(record, CatalogProductRecord | SupplierRecord):
            raise ValueError("Unknown catalog projection record")
        if record.tenant_id != tenant_id:
            raise ValueError("Catalog record does not match snapshot scope")
    for record in components.get("inventory", ()):
        if not isinstance(record, InventoryBatchRecord):
            raise ValueError("Unknown inventory projection record")
        if record.tenant_id != tenant_id or record.branch_id != branch_id:
            raise ValueError("Inventory record does not match snapshot scope")
