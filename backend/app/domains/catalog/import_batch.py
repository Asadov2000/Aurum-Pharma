"""Typed data for bounded import batches; no storage or transaction behavior."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

IMPORT_BATCH_SIZE = 250
IMPORT_BATCH_MIN_ROWS = 50
IMPORT_COLUMNS = (
    "brand_name",
    "inn",
    "manufacturer",
    "form",
    "dosage",
    "pack_size",
    "atx_code",
    "dispensing_type",
    "storage_type",
    "category",
    "base_price",
)


class ImportBatchFallback(Exception):
    """This chunk needs the existing sequential per-row semantics."""


class ImportKey(NamedTuple):
    name: str
    manufacturer: str | None
    dosage: str | None
    pack_size: str | None


def import_key(
    brand_name: str,
    manufacturer: str | None,
    dosage: str | None,
    pack_size: str | None,
) -> ImportKey:
    return ImportKey(brand_name.lower(), manufacturer, dosage, pack_size)


@dataclass(frozen=True)
class ImportRow:
    key: ImportKey
    fields: dict[str, object]
    barcode: str | None

    @property
    def brand_name(self) -> str:
        return str(self.fields["brand_name"])


@dataclass(frozen=True)
class ImportCandidate:
    id: UUID
    key: ImportKey


@dataclass(frozen=True)
class ImportCounts:
    created: int
    updated: int
    skipped: int


def _optional_text(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ImportBatchFallback
    return value


def import_row(source: Mapping[str, object]) -> ImportRow:
    """Adapt validated parser output without permitting additional DB columns."""
    if set(source) - {*IMPORT_COLUMNS, "barcode"}:
        raise ImportBatchFallback
    brand_name = source.get("brand_name")
    if not isinstance(brand_name, str) or not brand_name:
        raise ImportBatchFallback
    fields: dict[str, object] = {}
    for column in IMPORT_COLUMNS:
        value = source.get(column)
        if column == "base_price":
            if value is not None and not isinstance(value, Decimal):
                raise ImportBatchFallback
            fields[column] = value
        else:
            fields[column] = _optional_text(value)
    return ImportRow(
        import_key(
            brand_name,
            _optional_text(source.get("manufacturer")),
            _optional_text(source.get("dosage")),
            _optional_text(source.get("pack_size")),
        ),
        fields,
        _optional_text(source.get("barcode")),
    )
