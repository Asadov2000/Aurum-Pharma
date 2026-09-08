"""Shared manual validation must not widen the file import write contract."""

from __future__ import annotations

import csv
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Literal

import pytest
from openpyxl import Workbook

from app.domains.catalog.import_batch import import_row
from app.domains.catalog.import_parser import parse_import


@pytest.mark.parametrize("file_kind", ["csv", "xlsx"])
@pytest.mark.parametrize("extra_metadata", [False, True])
def test_parser_preserves_exact_import_columns_and_batch_compatibility(
    file_kind: Literal["csv", "xlsx"], extra_metadata: bool
) -> None:
    header = ["brand_name", "base_price", "barcode"]
    values = ["  Medicine  ", "12.50", "4607013192829"]
    if extra_metadata:
        header += ["operation_id", "initial_barcode", "create_request_fingerprint"]
        values += ["invalid-operation", "untrusted-metadata", "invalid-digest"]
    if file_kind == "csv":
        stream = StringIO()
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerow(values)
        raw = stream.getvalue().encode("utf-8")
    else:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet()
        sheet.append(header)
        sheet.append(values)
        buffer = BytesIO()
        workbook.save(buffer)
        workbook.close()
        raw = buffer.getvalue()

    rows, errors = parse_import(raw, f"catalog.{file_kind}")

    assert errors == []
    assert rows == [
        {
            "brand_name": "Medicine",
            "inn": None,
            "manufacturer": None,
            "form": None,
            "dosage": None,
            "pack_size": None,
            "atx_code": None,
            "dispensing_type": "otc",
            "storage_type": "normal",
            "category": None,
            "base_price": Decimal("12.50"),
            "barcode": "4607013192829",
        }
    ]
    adapted = import_row(rows[0])
    assert adapted.barcode == "4607013192829"
    assert adapted.fields == {key: value for key, value in rows[0].items() if key != "barcode"}
