"""Generate frontend/e2e/fixtures/import-sample.xlsx for the catalog-import
Playwright scenario. The output .xlsx is committed; this script documents how
it was produced and lets us regenerate it. openpyxl lives in the backend
image, so run it there and copy the result out:

    docker compose cp frontend/e2e/fixtures/gen_import_sample.py backend:/tmp/gen.py
    docker compose exec -T backend python /tmp/gen.py /tmp/import-sample.xlsx
    docker compose cp backend:/tmp/import-sample.xlsx frontend/e2e/fixtures/import-sample.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook

# No barcode column on purpose: the e2e tenant is shared across runs and the
# (tenant_id, code) barcode unique constraint would collide on a re-run. The
# numeric-barcode parsing path is covered by backend unit tests instead.
HEADER = [
    "brand_name",
    "inn",
    "manufacturer",
    "dosage",
    "pack_size",
    "dispensing_type",
    "base_price",
]
ROWS = [
    ["ИмпортXLSX Аспирин", "acetylsalicylic acid", "Bayer", "500mg", "10 tab", "otc", 12.50],
    ["ИмпортXLSX Парацетамол", "paracetamol", "GSK", "500mg", "20 tab", "otc", 8.75],
    ["ИмпортXLSX Ибупрофен", "ibuprofen", "Borisov", "200mg", "30 tab", "otc", 15.00],
]


def main(dest: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "catalog"
    ws.append(HEADER)
    for row in ROWS:
        ws.append(row)
    wb.save(dest)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("import-sample.xlsx"))
    main(out)
