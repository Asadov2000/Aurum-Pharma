"""Shared execution boundaries for catalog preview and import jobs."""

from uuid import UUID

CATALOG_EXECUTION_TIMEOUT_SECONDS = 600
CATALOG_ACTIVE_STATUSES = ("previewing", "importing")
MAX_IMPORT_FILE_BYTES = 10 * 1024 * 1024
IMPORT_SOFT_TIME_LIMIT_SECONDS = 120
IMPORT_HARD_TIME_LIMIT_SECONDS = 150


def catalog_execution_lock_key(job_id: UUID | str) -> str:
    return f"catalog-execution:{job_id}"
