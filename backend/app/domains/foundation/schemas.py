"""Pydantic v2 schemas for tenant / settings / branch / register."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

# -----------------------------------------------------------------------------
# Tenant
# -----------------------------------------------------------------------------


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = None
    inn_or_tin: str | None = None
    registration_number: str | None = None
    contact_email: EmailStr
    contact_phone: str | None = None
    legal_address: str | None = None


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = None
    inn_or_tin: str | None = None
    registration_number: str | None = None
    contact_email: EmailStr | None = None
    contact_phone: str | None = None
    legal_address: str | None = None
    logo_url: str | None = None
    status: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str | None) -> str | None:
        allowed = {"setup", "trial", "active", "grace_period", "readonly", "archived"}
        if v is not None and v not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return v


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    legal_name: str | None
    inn_or_tin: str | None
    registration_number: str | None
    contact_email: str
    contact_phone: str | None
    legal_address: str | None
    logo_url: str | None
    status: str
    setup_started_at: datetime
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    drug_catalog_mode: str
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Tenant settings
# -----------------------------------------------------------------------------


class ExpiryThresholds(BaseModel):
    """yellow > orange > red, each in months."""

    yellow: int = Field(ge=1, le=24)
    orange: int = Field(ge=1, le=24)
    red: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def _check_order(self) -> ExpiryThresholds:
        if not (self.yellow >= self.orange >= self.red):
            raise ValueError("expiry_thresholds must satisfy yellow >= orange >= red")
        return self


class TenantSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    expiry_thresholds: dict[str, int]
    expired_sale_mode: str
    refund_reason_mode: str
    session_admin_minutes: int
    session_pos_minutes: int
    pin_mode_enabled: bool
    draft_sale_lifetime_min: int
    prescription_warning_text: str
    updated_at: datetime


class TenantSettingsUpdate(BaseModel):
    expiry_thresholds: ExpiryThresholds | None = None
    expired_sale_mode: str | None = None
    refund_reason_mode: str | None = None
    session_admin_minutes: int | None = Field(default=None, ge=30, le=1440)
    session_pos_minutes: int | None = Field(default=None, ge=30, le=1440)
    pin_mode_enabled: bool | None = None
    draft_sale_lifetime_min: int | None = Field(default=None, ge=5, le=240)
    prescription_warning_text: str | None = None

    @field_validator("expired_sale_mode")
    @classmethod
    def _check_sale_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in {"strict", "warning", "off"}:
            raise ValueError("expired_sale_mode must be one of strict|warning|off")
        return v

    @field_validator("refund_reason_mode")
    @classmethod
    def _check_refund_mode(cls, v: str | None) -> str | None:
        allowed = {"required", "required_with_text", "optional", "off"}
        if v is not None and v not in allowed:
            raise ValueError(f"refund_reason_mode must be one of {sorted(allowed)}")
        return v


# -----------------------------------------------------------------------------
# Branch
# -----------------------------------------------------------------------------


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    branch_type: str = "pharmacy"
    license_number: str | None = None
    license_expires_at: date | None = None
    working_hours: dict[str, Any] | None = None
    receipt_header: dict[str, Any] | None = None

    @field_validator("branch_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in {"pharmacy", "pharmacy_post", "kiosk"}:
            raise ValueError("branch_type must be one of pharmacy|pharmacy_post|kiosk")
        return v


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = None
    branch_type: str | None = None
    license_number: str | None = None
    license_expires_at: date | None = None
    working_hours: dict[str, Any] | None = None
    receipt_header: dict[str, Any] | None = None
    is_active: bool | None = None

    @field_validator("branch_type")
    @classmethod
    def _check_type(cls, v: str | None) -> str | None:
        if v is not None and v not in {"pharmacy", "pharmacy_post", "kiosk"}:
            raise ValueError("branch_type must be one of pharmacy|pharmacy_post|kiosk")
        return v


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    address: str | None
    branch_type: str
    license_number: str | None
    license_expires_at: date | None
    working_hours: dict[str, Any] | None
    receipt_header: dict[str, Any] | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Register
# -----------------------------------------------------------------------------


class RegisterCreate(BaseModel):
    branch_id: UUID
    name: str = Field(min_length=1, max_length=200)
    printer_type: str | None = None
    printer_config: dict[str, Any] | None = None

    @field_validator("printer_type")
    @classmethod
    def _check_printer(cls, v: str | None) -> str | None:
        if v is not None and v not in {"browser", "thermal_58", "thermal_80", "a4"}:
            raise ValueError("printer_type must be one of browser|thermal_58|thermal_80|a4")
        return v


class RegisterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    printer_type: str | None = None
    printer_config: dict[str, Any] | None = None
    is_active: bool | None = None

    @field_validator("printer_type")
    @classmethod
    def _check_printer(cls, v: str | None) -> str | None:
        if v is not None and v not in {"browser", "thermal_58", "thermal_80", "a4"}:
            raise ValueError("printer_type must be one of browser|thermal_58|thermal_80|a4")
        return v


class RegisterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    name: str
    printer_type: str | None
    printer_config: dict[str, Any] | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
