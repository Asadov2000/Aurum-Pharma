"""Reproducible Linux parser resource benchmark; synthetic data, no DB/network.

Run from the backend directory inside an isolated container limited with
``--memory=768m --memory-swap=768m --cpus=1 --network=none``::

    python scripts/benchmark_catalog_import.py --work-dir .benchmark-catalog

Each case uses a fresh process. RSS includes Python/imports/input/output; CPU
and wall time cover input reading and parsing, excluding fixture generation.
This measures preview parsing, not Celery aggregate RSS or database import.
The 10 MiB storage boundary uses the real reader with a local fake transport.
JSON lines contain counts/timings only. A failed expectation exits nonzero.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import Workbook

# Direct script execution must resolve the same application as a worker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.catalog.import_limits import MAX_IMPORT_FILE_BYTES
from app.domains.catalog.import_parser import (
    EXPECTED_COLUMNS,
    MAX_IMPORT_COLUMNS,
    MAX_IMPORT_ROWS,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    parse_import,
)

CASES = (
    "csv_50k",
    "xlsx_50k",
    "csv_width_limit",
    "xlsx_width_limit",
    "csv_width_rejected",
    "xlsx_width_rejected",
    "xlsx_expansion_rejected",
    "storage_size_rejected",
)


def _fixture(path: Path, case: str) -> None:
    if case == "storage_size_rejected":
        with path.open("wb") as stream:
            for _ in range(MAX_IMPORT_FILE_BYTES // 1024):
                stream.write(b"x" * 1024)
            stream.write(b"x")
        return
    if case == "xlsx_expansion_rejected":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            with archive.open("xl/worksheets/sheet1.xml", "w") as stream:
                for _ in range(MAX_XLSX_UNCOMPRESSED_BYTES // 65536 + 1):
                    stream.write(b"x" * 65536)
        return
    columns = list(EXPECTED_COLUMNS)
    row_count = MAX_IMPORT_ROWS if case.endswith("50k") else 1
    wide_row: list[str] | None = None
    if "width" in case:
        width = MAX_IMPORT_COLUMNS + int(case.endswith("rejected"))
        columns = ["brand_name", *(f"unused_{i}" for i in range(width - 1))]
        # One adversarial record close to each format's per-cell boundary.
        # Unknown columns still consume parser resources before they are ignored.
        cell_length = csv.field_size_limit() - 1 if case.startswith("csv") else 32767
        wide_row = ["S" * 500, *(["x" * cell_length] * (width - 1))]
    if case.startswith("csv"):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(wide_row or _row(i, len(columns)) for i in range(row_count))
    else:
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet()
        sheet.append(columns)
        for index in range(row_count):
            sheet.append(wide_row or _row(index, len(columns)))
        workbook.save(path)
        workbook.close()


def _row(index: int, width: int) -> list[str]:
    # Prices and barcodes are intentionally absent; other values are synthetic.
    row = [
        f"Synthetic {index:05}",
        "INN",
        "Maker",
        "Tablet",
        "10mg",
        "20",
        "A01",
        "otc",
        "normal",
        "Synthetic",
        "",
        "",
    ]
    return row + ["x"] * (width - len(row))


def _storage_rejection(raw: bytes) -> None:
    from app.core import catalog_worker_storage as storage

    response = Mock()
    response.read.return_value = raw
    client = Mock()
    client.get_object.return_value = response
    settings = SimpleNamespace(MINIO_BUCKET="synthetic")
    with (
        patch.object(storage, "get_catalog_worker_settings", return_value=settings),
        patch.object(storage, "get_catalog_minio", return_value=client),
    ):
        try:
            storage.get_catalog_object(
                "synthetic/benchmark/imports/file.csv", tenant_id="benchmark"
            )
        finally:
            response.read.assert_called_once_with(MAX_IMPORT_FILE_BYTES + 1)
            response.close.assert_called_once_with()
            response.release_conn.assert_called_once_with()


def _measure(case: str, path: Path) -> int:
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    raw = path.read_bytes()
    rows_count = errors_count = 0
    rejection = ""
    try:
        if case == "storage_size_rejected":
            _storage_rejection(raw)
        else:
            rows, errors = parse_import(raw, path.name)
            rows_count, errors_count = len(rows), len(errors)
    except ValueError as exc:
        rejection = str(exc)
    expected_rejection = case.endswith("rejected")
    expected_rows = MAX_IMPORT_ROWS if case.endswith("50k") else 1
    if "width_rejected" in case:
        passed = f"more than {MAX_IMPORT_COLUMNS} columns" in rejection
    elif case == "xlsx_expansion_rejected":
        passed = "expands beyond" in rejection
    elif case == "storage_size_rejected":
        passed = "exceeds the file size limit" in rejection
    else:
        passed = not rejection and rows_count == expected_rows and errors_count == 0
    if not expected_rejection:
        passed = passed and len(raw) <= MAX_IMPORT_FILE_BYTES
    print(
        json.dumps(
            {
                "case": case,
                "passed": passed,
                "input_bytes": len(raw),
                "valid_rows": rows_count,
                "error_rows": errors_count,
                "rejected": bool(rejection),
                "wall_seconds": round(time.perf_counter() - start_wall, 4),
                "cpu_seconds": round(time.process_time() - start_cpu, 4),
                "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
            }
        ),
        flush=True,
    )
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path(".benchmark-catalog"))
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    parser.add_argument("--input", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Run in Linux: ru_maxrss is interpreted in KiB.")
    if args.case:
        if args.input is None:
            parser.error("--input is required with --case")
        return _measure(args.case, args.input)
    workspace = Path.cwd().resolve()
    directory = args.work_dir.resolve()
    if not directory.is_relative_to(workspace):
        parser.error("--work-dir must stay inside the current workspace directory")
    directory.mkdir(parents=True, exist_ok=True)
    # openpyxl write-only worksheets also create temporary XML files.
    tempfile.tempdir = str(directory)
    print(
        json.dumps(
            {
                "environment": "linux",
                "python": sys.version.split()[0],
                "memory_max": _cgroup_limit("memory.max"),
                "cpu_max": _cgroup_limit("cpu.max"),
                "measurement_scope": (
                    "fresh parser subprocess; synthetic inputs; no database or network"
                ),
            }
        ),
        flush=True,
    )
    failed = False
    for case in CASES:
        suffix = ".xlsx" if case.startswith("xlsx") else ".csv"
        path = directory / f"{case}-{os.getpid()}{suffix}"
        try:
            _fixture(path, case)
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--case",
                    case,
                    "--input",
                    str(path),
                ],
                check=False,
                timeout=180,
            )
            failed = failed or result.returncode != 0
        except subprocess.TimeoutExpired:
            print(json.dumps({"case": case, "passed": False, "timed_out": True}), flush=True)
            failed = True
        finally:
            path.unlink(missing_ok=True)
    return int(failed)


def _cgroup_limit(filename: str) -> str:
    path = Path("/sys/fs/cgroup") / filename
    return path.read_text().strip() if path.is_file() else "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
