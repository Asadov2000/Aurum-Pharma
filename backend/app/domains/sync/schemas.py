"""Validated public contracts for Cloud/Edge shadow synchronization."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field

Checksum = str


class SyncEventEnvelope(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    event_id: UUID
    tenant_id: UUID
    branch_id: UUID
    origin_node_id: UUID
    writer_epoch: int = Field(gt=0)
    sequence: int = Field(gt=0)
    operation_id: UUID
    aggregate_type: str = Field(min_length=1, max_length=100)
    aggregate_id: UUID
    event_type: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(gt=0)
    occurred_at: datetime
    payload: dict[str, object]
    payload_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    stream_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    projection_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncPullResponse(BaseModel):
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    origin_node_id: UUID
    writer_epoch: int = Field(gt=0)
    effective_after_sequence: int = Field(ge=0)
    after_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    after_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    cloud_last_sequence: int = Field(ge=0)
    events: list[SyncEventEnvelope]
    has_more: bool


class SyncBootstrapChunkDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    first_sequence: int = Field(gt=0)
    last_sequence: int = Field(gt=0)
    event_count: int = Field(gt=0)
    after_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    after_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    payload_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncBootstrapManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    bootstrap_id: UUID
    edge_node_id: UUID
    tenant_id: UUID
    branch_id: UUID
    credential_kid: UUID
    origin_node_id: UUID
    writer_epoch: int = Field(gt=0)
    root_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    root_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_sequence: int = Field(ge=0)
    source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    chunk_size: int = Field(gt=0, le=100)
    chunks: list[SyncBootstrapChunkDescriptor]
    issued_at: datetime
    expires_at: datetime


class SyncBootstrapManifestRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: SyncBootstrapManifest
    manifest_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: Literal["hmac-sha256-edge-v1"]
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


class SyncBootstrapChunkRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bootstrap_id: UUID
    chunk_index: int = Field(ge=0)
    payload_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    events: list[SyncEventEnvelope]


class SyncActivationTenantSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    legal_name: str | None
    inn_or_tin: str | None
    registration_number: str | None
    legal_address: str | None
    logo_url: str | None
    status: Literal["setup", "trial", "active", "grace_period", "readonly", "archived"]
    drug_catalog_mode: Literal["connected", "autonomous"]
    suspended_at: datetime | None
    archived_at: datetime | None
    updated_at: datetime


class SyncActivationTenantSettingsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    expiry_thresholds: dict[str, int]
    expired_sale_mode: Literal["strict", "warning", "off"]
    refund_reason_mode: Literal["required", "required_with_text", "optional", "off"]
    session_admin_minutes: int = Field(ge=30, le=1440)
    session_pos_minutes: int = Field(ge=30, le=1440)
    pin_mode_enabled: bool
    draft_sale_lifetime_min: int = Field(ge=5, le=240)
    report_timezone: str = Field(min_length=1, max_length=100)
    prescription_warning_text: str
    updated_at: datetime


class SyncActivationBranchSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    branch_type: Literal["pharmacy", "pharmacy_post", "kiosk"]
    license_number: str | None
    license_expires_at: date | None
    working_hours: dict[str, object] | None
    receipt_header: dict[str, object] | None
    is_active: bool
    updated_at: datetime


class SyncActivationRegisterSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    name: str
    printer_type: Literal["browser", "thermal_58", "thermal_80", "a4"] | None
    is_active: bool
    updated_at: datetime


class SyncActivationFoundationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = 1
    currency: Literal["TJS"] = "TJS"
    tenant: SyncActivationTenantSnapshot
    settings: SyncActivationTenantSettingsSnapshot
    branch: SyncActivationBranchSnapshot
    register_snapshot: SyncActivationRegisterSnapshot = Field(alias="register")


class SyncActivationBootstrapManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profile: Literal["foundation_shadow_v1"] = "foundation_shadow_v1"
    readiness_eligible: Literal[False] = False
    activation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    register_id: UUID
    capability: Literal["cash_sale_v1"] = "cash_sale_v1"
    writer_epoch: int = Field(gt=0)
    previous_writer_epoch: int = Field(gt=0)
    previous_terminal_sequence: int = Field(ge=0)
    previous_terminal_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    previous_terminal_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_baseline_seq: int = Field(ge=0)
    foundation_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    activation_manifest_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    credential_kid: UUID
    issued_at: datetime
    expires_at: datetime


class SyncActivationBootstrapRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: SyncActivationBootstrapManifest
    foundation: SyncActivationFoundationSnapshot
    manifest_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    signature_algorithm: Literal["hmac-sha256-edge-activation-v1"]
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


type SyncActivationComponent = Literal[
    "authorization",
    "catalog",
    "inventory",
    "offline_auth",
    "pos_materialization",
    "shift",
]


class SyncActivationBootstrapChunkMetadata(BaseModel):
    """Hash-bound metadata for one future full-profile payload chunk."""

    model_config = ConfigDict(extra="forbid")

    component: SyncActivationComponent
    schema_version: Literal[1] = 1
    chunk_index: int = Field(ge=0)
    item_count: int = Field(ge=0)
    payload_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncActivationBootstrapComponentMetadata(BaseModel):
    """Immutable descriptor committed into the full-profile component root."""

    model_config = ConfigDict(extra="forbid")

    component: SyncActivationComponent
    schema_version: Literal[1] = 1
    item_count: int = Field(ge=0)
    chunk_count: int = Field(gt=0)
    component_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: UUID
    branch_id: UUID
    display_name: str = Field(min_length=1, max_length=120)
    credential_valid_days: int = Field(default=90, ge=1, le=365)


class SyncNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID | None
    mode: Literal["shadow_readonly", "edge_writer"]
    status: Literal["active", "revoked"]
    display_name: str
    credential_kid: UUID
    credential_issued_at: datetime
    credential_expires_at: datetime
    shadow_start_origin_node_id: UUID
    shadow_start_writer_epoch: int = Field(gt=0)
    shadow_start_sequence: int
    shadow_start_checksum: Checksum
    shadow_start_projection_checksum: Checksum
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SyncNodeCredentialRead(SyncNodeRead):
    credential: str = Field(min_length=1)


class SyncCredentialRotate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_valid_days: int = Field(default=90, ge=1, le=365)


class SyncWriterPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_id: UUID4
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    register_id: UUID
    expected_writer_epoch: int = Field(gt=0)
    expected_sequence: int = Field(ge=0)
    expected_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    expected_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncWriterReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_id: UUID4
    writer_epoch: int = Field(gt=0)
    previous_sequence: int = Field(ge=0)
    previous_source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    previous_projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    bootstrap_snapshot_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    activation_manifest_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_baseline_seq: int = Field(ge=0)


class SyncWriterTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    branch_id: UUID
    activation_manifest_hash: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncWriterActivationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    writer_epoch: int
    writer_node_id: UUID
    allowed_register_id: UUID
    capability: Literal["cash_sale_v1"]
    state: Literal["prepared", "ready", "aborted", "activated"]
    root_source_checksum: Checksum
    root_projection_checksum: Checksum
    last_sequence: int
    current_source_checksum: Checksum
    current_projection_checksum: Checksum
    previous_writer_epoch: int
    previous_terminal_sequence: int
    previous_terminal_source_checksum: Checksum
    previous_terminal_projection_checksum: Checksum
    bootstrap_snapshot_hash: Checksum
    activation_manifest_hash: Checksum
    receipt_baseline_seq: int
    prepare_request_hash: Checksum
    prepared_at: datetime
    ready_at: datetime | None
    activated_at: datetime | None
    aborted_at: datetime | None


class SyncWriterEpochRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    writer_epoch: int
    writer_node_id: UUID
    allowed_register_id: UUID | None
    capability: Literal["cloud_full", "cash_sale_v1"]
    state: Literal["active", "fenced"]
    root_source_checksum: Checksum
    root_projection_checksum: Checksum
    last_sequence: int
    current_source_checksum: Checksum
    current_projection_checksum: Checksum
    previous_writer_epoch: int | None
    previous_terminal_sequence: int | None
    previous_terminal_source_checksum: Checksum | None
    previous_terminal_projection_checksum: Checksum | None
    bootstrap_snapshot_hash: Checksum
    activation_manifest_hash: Checksum
    receipt_baseline_seq: int
    prepared_at: datetime
    activated_at: datetime | None
    fenced_at: datetime | None


class SyncWriterReadinessRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    register_id: UUID
    writer_epoch: int
    previous_sequence: int
    previous_source_checksum: Checksum
    previous_projection_checksum: Checksum
    bootstrap_snapshot_hash: Checksum
    activation_manifest_hash: Checksum
    receipt_baseline_seq: int
    request_hash: Checksum
    reported_at: datetime


class SyncShadowReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID4
    origin_node_id: UUID
    writer_epoch: int = Field(gt=0)
    last_sequence: int = Field(ge=0)
    source_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")
    projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncShadowReportRead(BaseModel):
    report_id: UUID
    status: Literal["matched", "mismatch"]
    last_sequence: int
    source_checksum: Checksum
    expected_source_checksum: Checksum
    source_verified: bool
    projection_checksum: Checksum
    expected_checksum: Checksum


class EdgeApplyResult(BaseModel):
    applied: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    source_checksum: Checksum
    projection_checksum: Checksum
    status: Literal["synced", "gap", "quarantined", "mismatch"]
