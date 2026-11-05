"""Commands contain identifiers/evidence only; allocation is the saved server plan."""

from typing import Literal

from pydantic import UUID4, Field

from app.domains.pos.unit_contracts import FrozenContract


class PrepareElectronicCheckoutAttempts(FrozenContract):
    attempt_ids: dict[Literal["card", "qr"], UUID4] = Field(min_length=1, max_length=2)


class ElectronicCheckoutCommand(FrozenContract):
    """An explicit empty command; caller cannot replace amounts or payment methods."""
