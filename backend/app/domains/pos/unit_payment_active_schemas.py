"""Active preparation discovery, without payment acceptance or draft mutation."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_payment_binding_schemas import UnitPaymentBindingRead
from app.domains.pos.unit_payment_intent_schemas import SavedUnitPaymentIntent


class UnitPaymentActiveRead(FrozenContract):
    sale_id: UUID
    intent: SavedUnitPaymentIntent | None
    binding: UnitPaymentBindingRead | None

    @model_validator(mode="after")
    def coherent_state(self) -> Self:
        if (self.intent is None) != (self.binding is None):
            raise ValueError("Active preparation requires both intent and binding")
        if self.intent is not None and self.binding is not None:
            if (
                self.binding.state != "bound"
                or self.binding.sale_id != self.sale_id
                or self.intent.plan.quote.sale_id != self.sale_id
                or self.binding.operation_id != self.intent.operation_id
                or self.binding.plan_hash != self.intent.plan.plan_hash
            ):
                raise ValueError("Active preparation snapshot is inconsistent")
        return self
