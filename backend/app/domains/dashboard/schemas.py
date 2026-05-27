"""Pydantic schemas for the dashboard summary.

One response object carries all four sections so the owner home screen
makes a single request instead of fanning out to 8 endpoints.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class TodaySection(BaseModel):
    revenue: Decimal
    currency: str
    receipts: int
    active_shifts: int
    cashiers_on_shift: int


class ExpiringBatch(BaseModel):
    id: UUID
    batch_number: str | None
    branch_id: UUID
    expires_at: date
    days_to_expiry: int
    expiry_status: str  # expired | red | orange | yellow
    qty_remaining: Decimal


class ExpiringLicense(BaseModel):
    branch_id: UUID
    branch_name: str
    license_expires_at: date
    days_left: int


class ExpiringSection(BaseModel):
    batches: list[ExpiringBatch]
    licenses: list[ExpiringLicense]


class FinanceSection(BaseModel):
    subscription_status: str | None
    subscription_period_end: datetime | None
    open_invoices_count: int
    open_invoices_total: Decimal
    currency: str
    has_overdue: bool


class ChecklistSection(BaseModel):
    draft_incoming_count: int
    closed_shifts_count: int
    latest_closed_shift_id: UUID | None


class DashboardSummary(BaseModel):
    today: TodaySection
    expiring: ExpiringSection
    finance: FinanceSection
    checklist: ChecklistSection
    generated_at: datetime
