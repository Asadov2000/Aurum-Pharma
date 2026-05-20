"""Pydantic schemas for the audit domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    user_id: UUID | None
    action: str
    table_name: str
    record_id: UUID | None
    old_values: dict[str, Any] | None
    new_values: dict[str, Any] | None
    changed_fields: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    metadata: dict[str, Any] | None = None
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    page: int
    page_size: int
