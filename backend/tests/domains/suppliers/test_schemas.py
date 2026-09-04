"""Boundary validation for supplier write contracts."""

from __future__ import annotations

from uuid import uuid1, uuid4

import pytest
from pydantic import ValidationError

from app.domains.suppliers.schemas import SupplierCreate, SupplierReturnCreate


@pytest.mark.parametrize(
    ("field_name", "max_length"),
    [
        ("name", 200),
        ("legal_name", 300),
        ("inn_or_tin", 40),
        ("contact_person", 200),
        ("phone", 50),
        ("address", 500),
        ("notes", 2000),
    ],
)
def test_supplier_create_enforces_text_boundaries(
    field_name: str,
    max_length: int,
) -> None:
    base: dict[str, object] = {"operation_id": uuid4(), "name": "Supplier"}
    accepted = SupplierCreate.model_validate({**base, field_name: "x" * max_length})
    assert len(getattr(accepted, field_name)) == max_length

    with pytest.raises(ValidationError):
        SupplierCreate.model_validate({**base, field_name: "x" * (max_length + 1)})


def test_supplier_create_requires_uuid4_operation_id() -> None:
    with pytest.raises(ValidationError):
        SupplierCreate.model_validate({"name": "Supplier"})

    with pytest.raises(ValidationError):
        SupplierCreate.model_validate({"operation_id": uuid1(), "name": "Supplier"})

    payload = SupplierCreate.model_validate(
        {"operation_id": uuid4(), "name": "  Somon   Medical  "}
    )
    assert payload.name == "Somon Medical"


def test_supplier_return_comment_boundary() -> None:
    base = {
        "operation_id": uuid4(),
        "supplier_id": uuid4(),
        "batch_id": uuid4(),
        "qty": "1",
        "reason": "other",
    }
    accepted = SupplierReturnCreate.model_validate({**base, "comment": "x" * 2000})
    assert accepted.comment == "x" * 2000

    with pytest.raises(ValidationError):
        SupplierReturnCreate.model_validate({**base, "comment": "x" * 2001})
