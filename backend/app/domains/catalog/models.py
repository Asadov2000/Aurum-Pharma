"""SQLAlchemy models for the catalog domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the catalog domain."""


class MasterCatalog(Base):
    __tablename__ = "master_catalog"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    brand_name: Mapped[str] = mapped_column(Text, nullable=False)
    inn: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    form: Mapped[str | None] = mapped_column(Text)
    dosage: Mapped[str | None] = mapped_column(Text)
    pack_size: Mapped[str | None] = mapped_column(Text)
    atx_code: Mapped[str | None] = mapped_column(Text)
    dispensing_type: Mapped[str | None] = mapped_column(Text)
    storage_type: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TenantCatalog(Base):
    __tablename__ = "tenant_catalog"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    master_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("master_catalog.id")
    )
    brand_name: Mapped[str] = mapped_column(Text, nullable=False)
    inn: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    form: Mapped[str | None] = mapped_column(Text)
    dosage: Mapped[str | None] = mapped_column(Text)
    pack_size: Mapped[str | None] = mapped_column(Text)
    atx_code: Mapped[str | None] = mapped_column(Text)
    dispensing_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'otc'"))
    storage_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'normal'"))
    category: Mapped[str | None] = mapped_column(Text)
    base_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    import_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "dispensing_type IN ('prescription','otc','special')",
            name="ck_tc_dispensing_type",
        ),
        CheckConstraint(
            "storage_type IN ('normal','cold','frozen')",
            name="ck_tc_storage_type",
        ),
        CheckConstraint("base_price IS NULL OR base_price >= 0", name="ck_tc_base_price"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_catalog_tenant_id_id"),
    )


class Barcode(Base):
    __tablename__ = "barcode"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ean13'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_barcode_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_id"],
            ["tenant_catalog.tenant_id", "tenant_catalog.id"],
            name="fk_barcode_tenant_catalog",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "code_type IN ('ean13','ean8','gs1_128','code128','qr','other')",
            name="ck_barcode_code_type",
        ),
    )


class CatalogImportJob(Base):
    __tablename__ = "catalog_import_job"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_filename: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    duplicate_strategy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'skip'")
    )
    total_rows: Mapped[int | None] = mapped_column(Integer)
    valid_rows: Mapped[int | None] = mapped_column(Integer)
    error_rows: Mapped[int | None] = mapped_column(Integer)
    preview_data: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at_for_rollback: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','validating','importing','success','failed','rolled_back')",
            name="ck_cij_status",
        ),
        CheckConstraint(
            "duplicate_strategy IN ('skip','update','create_copy')",
            name="ck_cij_duplicate_strategy",
        ),
    )
