"""A mixed electronic preflight must not erase legacy rows or mutate its frozen quote."""

from decimal import Decimal
from uuid import UUID

import pytest

from app.core.errors import BusinessRuleError
from app.domains.pos.mixed_electronic_checkout_preparation import mixed_electronic_lines
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2


def quote() -> MixedCartQuoteV2:
    return MixedCartQuoteV2.model_validate(
        {
            **{
                field: UUID(int=index)
                for index, field in enumerate(
                    ("tenant_id", "sale_id", "branch_id", "register_id", "shift_id", "cashier_id"),
                    start=1,
                )
            },
            "total_amount": "23.52",
            "lines": [
                {
                    "inventory_kind": "legacy_pack",
                    "id": UUID(int=40),
                    "position": 1,
                    "catalog_id": UUID(int=41),
                    "batch_id": UUID(int=42),
                    "quantity": "0.001",
                    "original_retail_unit_price": "25.00",
                    "gross_amount": "0.02",
                    "discount_amount": "0.00",
                    "net_amount": "0.02",
                },
                {
                    "inventory_kind": "physical_unit",
                    "id": UUID(int=20),
                    "position": 2,
                    "catalog_id": UUID(int=21),
                    "quantity": 2,
                    "sale_format": "blister",
                    "packaging": {
                        "version": 2,
                        "unit": "tablet",
                        "units_per_pack": 30,
                        "units_per_blister": 10,
                        "dispense_step": 1,
                    },
                    "base_units": 20,
                    "original_retail_unit_price": "12.50",
                    "gross_amount": "25.00",
                    "discount_amount": "1.50",
                    "net_amount": "23.50",
                },
            ],
        }
    )


def test_mixed_preflight_retains_exact_physical_packaging_and_all_legacy_facts() -> None:
    saved = quote()
    before = saved.model_dump_json()
    lines = mixed_electronic_lines(saved)
    assert len(lines) == 1
    assert lines[0].id == saved.lines[1].id
    assert lines[0].catalog_id == saved.lines[1].catalog_id
    assert lines[0].quantity == saved.lines[1].quantity
    assert lines[0].packaging.model_dump() == saved.lines[1].model_dump()["packaging"]
    assert saved.lines[0].inventory_kind == "legacy_pack"
    assert saved.lines[0].quantity == Decimal("0.001")
    assert saved.model_dump_json() == before


@pytest.mark.parametrize("index", [0, 1])
def test_mixed_preflight_does_not_accept_a_single_stock_kind(index: int) -> None:
    saved = quote()
    line = saved.lines[index].model_copy(update={"position": 1})
    only = MixedCartQuoteV2.model_validate(
        {**saved.model_dump(), "lines": [line], "total_amount": line.net_amount}
    )
    with pytest.raises(BusinessRuleError, match="legacy and physical"):
        mixed_electronic_lines(only)
