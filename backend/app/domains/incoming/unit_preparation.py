"""Receiving preparation metadata; existing stock remains counted in packs."""

from decimal import Decimal

from app.core.errors import BusinessRuleError
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_stock import UnitPackaging


def validate_prepared_pack_quantity(packaging: UnitPackaging, quantity: Decimal) -> None:
    """Reject rounding and overflow before a prepared incoming line is written."""
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity > MAX_BASE_UNITS
        or quantity != quantity.to_integral_value()
        or int(quantity) * packaging.units_per_pack > MAX_BASE_UNITS
    ):
        raise BusinessRuleError(
            "Prepared incoming quantity must be whole packs within the unit stock limit"
        )
