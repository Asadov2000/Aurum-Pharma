"""Immutable operator-recorded terminal evidence, separate from financial posting."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.pos.mixed_electronic_refund_binding_models import Base, BindingFields


class MixedElectronicRefundOutcome(BindingFields, Base):
    __tablename__ = "pos_mixed_electronic_refund_outcome"

    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    original_payment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    terminal_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_number: Mapped[str] = mapped_column(Text, nullable=False)
