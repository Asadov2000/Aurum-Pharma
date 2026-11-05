"""Immutable activation marker; it is not terminal or payout evidence."""

from uuid import UUID

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domains.pos.mixed_electronic_refund_binding_models import Base, BindingFields


class MixedElectronicRefundActivation(BindingFields, Base):
    __tablename__ = "pos_mixed_electronic_refund_activation"

    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
