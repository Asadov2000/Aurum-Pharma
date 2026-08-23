"""Pydantic v2 schemas for tenant / settings / branch / register."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


class OwnerCreate(BaseModel):
    """Provision the first owner of a tenant (support-level onboarding)."""

    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)


class OwnerProvisionRead(BaseModel):
    user_id: UUID
    membership_id: UUID
    ownership_id: UUID
    email: str
    home_tenant_id: UUID
    role_id: UUID


# -----------------------------------------------------------------------------
# Tenant settings
# -----------------------------------------------------------------------------

POSPaymentMethod = Literal["cash", "card", "qr"]


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
    expired_sale_mode: Literal["strict"]
    refund_reason_mode: str
    session_admin_minutes: int
    session_pos_minutes: int
    pin_mode_enabled: bool
    pos_payment_methods: list[POSPaymentMethod] = Field(min_length=1, max_length=3)
    pos_mixed_payment_enabled: bool
    draft_sale_lifetime_min: int
    report_timezone: str
    prescription_warning_text: str
    version: int = Field(ge=1)
    updated_at: datetime

    @field_validator("pos_payment_methods")
    @classmethod
    def _check_unique_pos_payment_methods(
        cls,
        value: list[POSPaymentMethod],
    ) -> list[POSPaymentMethod]:
        if len(value) != len(set(value)):
            raise ValueError("pos_payment_methods must contain unique values")
        return value


class TenantSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    expiry_thresholds: ExpiryThresholds | None = None
    expired_sale_mode: Literal["strict"] | None = None
    refund_reason_mode: str | None = None
    session_admin_minutes: int | None = Field(default=None, ge=30, le=1440)
    session_pos_minutes: int | None = Field(default=None, ge=30, le=1440)
    pin_mode_enabled: bool | None = None
    pos_payment_methods: list[POSPaymentMethod] | None = Field(
        default=None,
        min_length=1,
        max_length=3,
    )
    pos_mixed_payment_enabled: bool | None = None
    draft_sale_lifetime_min: int | None = Field(default=None, ge=5, le=240)
    report_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    prescription_warning_text: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_change(self) -> TenantSettingsUpdate:
        changed = self.model_dump(exclude={"expected_version"}, exclude_none=True)
        if not changed:
            raise ValueError("at least one setting must be provided")
        return self

    @field_validator("refund_reason_mode")
    @classmethod
    def _check_refund_mode(cls, v: str | None) -> str | None:
        allowed = {"required", "required_with_text", "optional", "off"}
        if v is not None and v not in allowed:
            raise ValueError(f"refund_reason_mode must be one of {sorted(allowed)}")
        return v

    @field_validator("report_timezone")
    @classmethod
    def _check_report_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("report_timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("pos_payment_methods")
    @classmethod
    def _check_unique_pos_payment_methods(
        cls,
        value: list[POSPaymentMethod] | None,
    ) -> list[POSPaymentMethod] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("pos_payment_methods must contain unique values")
        return value


class TenantOperationalSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    expired_sale_mode: Literal["strict"]
    refund_reason_mode: Literal["required", "required_with_text", "optional", "off"]
    pos_payment_methods: list[POSPaymentMethod] = Field(min_length=1, max_length=3)
    pos_mixed_payment_enabled: bool
    draft_sale_lifetime_min: int
    report_timezone: str
    version: int = Field(ge=1)
    updated_at: datetime

    @field_validator("pos_payment_methods")
    @classmethod
    def _check_unique_pos_payment_methods(
        cls,
        value: list[POSPaymentMethod],
    ) -> list[POSPaymentMethod]:
        if len(value) != len(set(value)):
            raise ValueError("pos_payment_methods must contain unique values")
        return value


# -----------------------------------------------------------------------------
# Branch
# -----------------------------------------------------------------------------


class ReceiptHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    inn_or_tin: str | None = Field(default=None, max_length=50)
    demo_notice: str | None = Field(default=None, max_length=200)


class ReceiptHeaderRead(BaseModel):
    """Tolerant reader for receipt data saved before validation was introduced."""

    model_config = ConfigDict(extra="ignore")

    line1: str | None = None
    line2: str | None = None
    phone: str | None = None
    inn_or_tin: str | None = None
    demo_notice: str | None = None


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str | None = None
    branch_type: str = "pharmacy"
    license_number: str | None = None
    license_expires_at: date | None = None
    working_hours: dict[str, Any] | None = None
    receipt_header: ReceiptHeader | None = None

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
    receipt_header: ReceiptHeader | None = None
    is_active: bool | None = None

    @field_validator("branch_type")
    @classmethod
    def _check_type(cls, v: str | None) -> str | None:
        if v is not None and v not in {"pharmacy", "pharmacy_post", "kiosk"}:
            raise ValueError("branch_type must be one of pharmacy|pharmacy_post|kiosk")
        return v

    @model_validator(mode="after")
    def _reject_null_for_required_columns(self) -> BranchUpdate:
        for field_name in ("name", "branch_type", "is_active"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


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
    receipt_header: ReceiptHeaderRead | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BranchSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    branch_type: Literal["pharmacy", "pharmacy_post", "kiosk"] | None = None
    is_active: bool | None = Field(default=None, strict=True)
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=50, ge=1, le=200, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BranchSearchResponse(BaseModel):
    items: list[BranchRead]
    total: int
    page: int
    page_size: int


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


class RegisterSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str | None = Field(default=None, max_length=200)
    branch_id: UUID | None = None
    printer_type: Literal["browser", "thermal_58", "thermal_80", "a4"] | None = None
    is_active: bool | None = Field(default=None, strict=True)
    page: int = Field(default=1, ge=1, strict=True)
    page_size: int = Field(default=50, ge=1, le=200, strict=True)

    @field_validator("q")
    @classmethod
    def _normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RegisterSearchResponse(BaseModel):
    items: list[RegisterRead]
    total: int
    page: int
    page_size: int
