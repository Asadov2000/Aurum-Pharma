"""CSV import parser for catalog uploads.

Reads UTF-8 and Windows-1251 (cp1251) automatically: tries UTF-8 first,
falls back to cp1251 if it sees a UnicodeDecodeError. Column mapping is
case-insensitive header match against the expected names below.

Phase 2 will add openpyxl-backed XLSX parsing — until then xlsx uploads
return a parser error.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any

EXPECTED_COLUMNS = [
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
    "barcode",
]
DISPENSING = {"prescription", "otc", "special"}
STORAGE = {"normal", "cold", "frozen"}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode CSV; supported encodings: UTF-8 and cp1251")


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def parse_csv(  # noqa: PLR0912 — single-pass parser, splitting hurts clarity
    raw: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (rows, errors) where rows have validated, typed values keyed
    by EXPECTED_COLUMNS, and errors look like `{"row": N, "message": "..."}`."""
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")

    header_map = {_normalize_header(fn): fn for fn in reader.fieldnames if fn is not None}
    if "brand_name" not in header_map:
        raise ValueError("CSV must contain a 'brand_name' column")

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for line_no, row in enumerate(reader, start=2):  # start=2 → 1-based, +header
        parsed: dict[str, Any] = {}
        row_errors: list[str] = []

        for canonical in EXPECTED_COLUMNS:
            src = header_map.get(canonical)
            raw_val = (row.get(src) or "").strip() if src else ""
            if canonical == "base_price":
                if raw_val:
                    try:
                        parsed[canonical] = Decimal(raw_val.replace(",", "."))
                    except (InvalidOperation, ValueError):
                        row_errors.append(f"base_price '{raw_val}' is not a number")
                        parsed[canonical] = None
                else:
                    parsed[canonical] = None
            else:
                parsed[canonical] = raw_val or None

        # Required field
        if not parsed.get("brand_name"):
            row_errors.append("brand_name is required")

        # Enum-ish validation
        dt = parsed.get("dispensing_type")
        if dt and dt not in DISPENSING:
            row_errors.append(f"dispensing_type '{dt}' is invalid")
        elif not dt:
            parsed["dispensing_type"] = "otc"

        st = parsed.get("storage_type")
        if st and st not in STORAGE:
            row_errors.append(f"storage_type '{st}' is invalid")
        elif not st:
            parsed["storage_type"] = "normal"

        if row_errors:
            errors.append({"row": line_no, "messages": row_errors})
        else:
            rows.append(parsed)

    return rows, errors
