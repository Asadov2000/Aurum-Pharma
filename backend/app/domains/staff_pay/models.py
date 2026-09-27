"""Private ledgers; foreign keys and write capabilities are defined by migration."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StaffPayPeriod(Base):
    __tablename__ = "staff_pay_period"

    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID)
    employee_id: Mapped[UUID] = mapped_column(PG_UUID)
    membership_id: Mapped[UUID] = mapped_column(PG_UUID)
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID)
    create_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_by: Mapped[UUID] = mapped_column(PG_UUID)
    updated_by: Mapped[UUID] = mapped_column(PG_UUID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StaffPayEntry(Base):
    __tablename__ = "staff_pay_entry"

    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID)
    period_id: Mapped[UUID] = mapped_column(PG_UUID)
    kind: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    paid_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text)
    reverses_entry_id: Mapped[UUID | None] = mapped_column(PG_UUID)
    recorded_by: Mapped[UUID] = mapped_column(PG_UUID)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StaffPayRevision(Base):
    __tablename__ = "staff_pay_revision"

    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID)
    period_id: Mapped[UUID] = mapped_column(PG_UUID)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str] = mapped_column(Text)
    row_version: Mapped[int] = mapped_column(Integer)
    recorded_by: Mapped[UUID] = mapped_column(PG_UUID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
