"""Validation and authorization contract tests for catalog inputs."""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.domains.catalog.router import import_confirm, router
from app.domains.catalog.schemas import BarcodeCreate, CatalogItemCreate, CatalogItemUpdate


def test_catalog_input_normalizes_text_and_nullable_patch() -> None:
    created = CatalogItemCreate(
        brand_name="  Парацетамол  ",
        manufacturer="  Aurum  ",
        category="   ",
        base_price=Decimal("12.50"),
    )
    patch = CatalogItemUpdate(manufacturer=None, base_price=None)

    assert created.brand_name == "Парацетамол"
    assert created.manufacturer == "Aurum"
    assert created.category is None
    assert patch.model_dump(exclude_unset=True) == {
        "manufacturer": None,
        "base_price": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"brand_name": "   "},
        {"brand_name": "Valid", "base_price": Decimal("-0.01")},
        {"brand_name": "Valid", "base_price": Decimal("1.001")},
        {"brand_name": "Valid", "base_price": Decimal("1234567890123.00")},
    ],
)
def test_catalog_create_rejects_invalid_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CatalogItemCreate.model_validate(payload)


def test_catalog_update_rejects_null_or_blank_brand_name() -> None:
    with pytest.raises(ValidationError):
        CatalogItemUpdate(brand_name=None)
    with pytest.raises(ValidationError):
        CatalogItemUpdate(brand_name="   ")


def test_barcode_is_trimmed_and_cannot_be_blank() -> None:
    assert BarcodeCreate(code="  4607013192829  ").code == "4607013192829"
    with pytest.raises(ValidationError):
        BarcodeCreate(code="   ")


def test_import_confirm_requires_create_and_update_permissions() -> None:
    route = next(
        item
        for item in router.routes
        if isinstance(item, APIRoute) and item.endpoint is import_confirm
    )
    required_codes = {
        inspect.getclosurevars(dependency.call).nonlocals.get("code")
        for dependency in route.dependant.dependencies
        if dependency.call is not None and inspect.isfunction(dependency.call)
    }

    assert {"catalog.create", "catalog.update"}.issubset(required_codes)
