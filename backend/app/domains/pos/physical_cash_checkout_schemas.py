"""Public physical cash contracts; prices and ownership remain server-owned."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, Field

from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2
from app.domains.pos.unit_contracts import FrozenContract


class PhysicalCashOperationNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    sale_id: UUID


class PhysicalCashOperationSaved(FrozenContract):
    state: Literal["saved_draft"] = "saved_draft"
    operation_id: UUID4
    sale_id: UUID
    saved_intent: SavedCashIntentV2


class PhysicalCashOperationCompleted(FrozenContract):
    state: Literal["completed"] = "completed"
    operation_id: UUID4
    sale_id: UUID
    saved_intent: SavedCashIntentV2
    result: PhysicalCheckoutResultV3


PhysicalCashOperationRead = Annotated[
    PhysicalCashOperationNotFound | PhysicalCashOperationSaved | PhysicalCashOperationCompleted,
    Field(discriminator="state"),
]
