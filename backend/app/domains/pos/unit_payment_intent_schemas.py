"""Durable preparation of payment plans, without acceptance of money."""

from typing import Literal

from pydantic import UUID4, AwareDatetime

from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_payment_plan import UnitPaymentPlanRequest, ValidatedUnitPaymentPlan


class SaveUnitPaymentIntentRequest(FrozenContract):
    operation_id: UUID4
    request: UnitPaymentPlanRequest


class SavedUnitPaymentIntent(FrozenContract):
    state: Literal["saved_draft"] = "saved_draft"
    operation_id: UUID4
    saved_at: AwareDatetime
    plan: ValidatedUnitPaymentPlan
