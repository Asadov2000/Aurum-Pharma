"""Validated public contracts for Cloud/Edge shadow synchronization."""

from __future__ import annotations

from datetime import datetime
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
    mode: Literal["shadow_readonly"]
    status: Literal["active", "revoked"]
    display_name: str
    credential_kid: UUID
    credential_expires_at: datetime
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


class SyncShadowReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: UUID4
    origin_node_id: UUID
    writer_epoch: int = Field(gt=0)
    last_sequence: int = Field(ge=0)
    projection_checksum: Checksum = Field(pattern=r"^[0-9a-f]{64}$")


class SyncShadowReportRead(BaseModel):
    report_id: UUID
    status: Literal["matched", "mismatch"]
    last_sequence: int
    projection_checksum: Checksum
    expected_checksum: Checksum


class EdgeApplyResult(BaseModel):
    applied: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    source_checksum: Checksum
    projection_checksum: Checksum
    status: Literal["synced", "gap", "quarantined", "mismatch"]
