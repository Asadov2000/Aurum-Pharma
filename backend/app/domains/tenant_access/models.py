"""Persisted policy, separate from financial and account security state."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantAccessPolicy(Base):
    __tablename__ = "tenant_access_policy"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    blocked_modules: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    max_branches: Mapped[int | None] = mapped_column(Integer)
    max_users: Mapped[int | None] = mapped_column(Integer)
    max_registers: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
