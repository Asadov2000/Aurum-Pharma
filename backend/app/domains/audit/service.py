"""Business logic for the audit domain.

Triggers write rows automatically for every INSERT/UPDATE/DELETE on the
tables listed in migration 0010. The service layer adds three explicit
action types — VIEW, EXPORT, IMPERSONATE — that the rest of the app
calls when a human deliberately looks at, exports, or impersonates a
sensitive resource.

PII filter: triggers redact sensitive row snapshots before writing audit_log.
The service keeps the same scrubber as defense-in-depth for explicit metadata
and client-facing payloads.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from app.core.time import local_day_range
from app.domains.audit.models import AuditLog
from app.domains.audit.repository import AuditRepository
from app.domains.foundation.repository import FoundationRepository

logger = structlog.get_logger("audit.service")

DEFAULT_REPORT_TIMEZONE = "Asia/Dushanbe"
DateFilter = date | datetime


SENSITIVE_FIELDS: set[str] = {
    "password_hash",
    "totp_secret",
    "refresh_token_hash",
    "code_hash",
    "code_salt",
    "jwt_secret",
    "access_token",
    "refresh_token",
    "email",
    "email_lower",
    "phone",
    "recipient",
    "full_name",
    "owner_full_name",
    "patient_name",
    "doctor_name",
    "doctor_license",
    "prescription_number",
    "notes",
    "contact_person",
    "purchase_price",
}
SENSITIVE_SUFFIXES: tuple[str, ...] = ("_email", "_phone")
REDACTED_VALUE = "***"


def _is_sensitive_field(key: str) -> bool:
    key_lower = key.lower()
    return key_lower in SENSITIVE_FIELDS or key_lower.endswith(SENSITIVE_SUFFIXES)


def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_field(key) and item is not None:
                out[key] = REDACTED_VALUE
            else:
                out[key] = _redact_sensitive_payload(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


def _redact_dict(blob: dict[str, Any] | None) -> dict[str, Any] | None:
    if blob is None:
        return None
    return cast(dict[str, Any], _redact_sensitive_payload(blob))


def _safe_timezone(value: str | None) -> str:
    candidate = value or DEFAULT_REPORT_TIMEZONE
    try:
        ZoneInfo(candidate)
    except (ValueError, ZoneInfoNotFoundError):
        return DEFAULT_REPORT_TIMEZONE
    return candidate


def audit_date_bounds(
    *,
    date_from: DateFilter | None,
    date_to: DateFilter | None,
    report_timezone: str,
) -> tuple[datetime | None, datetime | None, datetime | None]:
    """Convert calendar filters to UTC bounds without losing the end date.

    The tuple contains ``(start_inclusive, end_inclusive, end_exclusive)``.
    Date-only values use a half-open local-day range. Datetime values remain
    inclusive for compatibility with older internal callers.
    """
    timezone = _safe_timezone(report_timezone)

    start_inclusive: datetime | None = None
    if date_from is not None:
        if isinstance(date_from, datetime):
            start_inclusive = date_from
        else:
            start_inclusive, _ = local_day_range(date_from, timezone)

    end_inclusive: datetime | None = None
    end_exclusive: datetime | None = None
    if date_to is not None:
        if isinstance(date_to, datetime):
            end_inclusive = date_to
        else:
            _, end_exclusive = local_day_range(date_to, timezone)

    return start_inclusive, end_inclusive, end_exclusive


class AuditService:
    def __init__(self, repo: AuditRepository) -> None:
        self.repo = repo

    # ---- search ----

    async def get_report_timezone(self, tenant_id: UUID | None) -> str:
        """Read the tenant's calendar timezone, with a safe pilot default."""
        if tenant_id is None:
            return DEFAULT_REPORT_TIMEZONE
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        return _safe_timezone(settings.report_timezone if settings is not None else None)

    async def search(
        self,
        *,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        table_name: str | None = None,
        record_id: UUID | None = None,
        date_from: DateFilter | None = None,
        date_to: DateFilter | None = None,
        report_timezone: str = DEFAULT_REPORT_TIMEZONE,
        page: int = 1,
        page_size: int = 50,
        global_scope: bool = False,
    ) -> tuple[list[AuditLog], int]:
        start_inclusive, end_inclusive, end_exclusive = audit_date_bounds(
            date_from=date_from,
            date_to=date_to,
            report_timezone=report_timezone,
        )
        return await self.repo.search(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            table_name=table_name,
            record_id=record_id,
            date_from=start_inclusive,
            date_to=end_inclusive,
            date_to_exclusive=end_exclusive,
            page=page,
            page_size=page_size,
            global_scope=global_scope,
        )

    # ---- explicit action helpers (write side) ----

    async def log_view(
        self,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        table_name: str,
        record_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repo.insert_entry(
            tenant_id=tenant_id,
            user_id=user_id,
            action="VIEW",
            table_name=table_name,
            record_id=record_id,
            metadata_json=_redact_dict(metadata),
        )

    async def log_export(
        self,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        what: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repo.insert_entry(
            tenant_id=tenant_id,
            user_id=user_id,
            action="EXPORT",
            table_name=what,
            record_id=None,
            metadata_json=_redact_dict(metadata),
        )

    async def log_impersonate(
        self,
        *,
        support_user_id: UUID,
        tenant_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        return await self.repo.insert_entry(
            tenant_id=tenant_id,
            user_id=support_user_id,
            action="IMPERSONATE",
            table_name="tenant",
            record_id=tenant_id,
            metadata_json=_redact_dict(metadata),
        )

    # ---- PII filter for read-side serialization ----

    @staticmethod
    def scrub(entry: AuditLog) -> dict[str, Any]:
        """Return a dict copy of `entry` with sensitive fields hidden.
        Trigger-generated rows should already be redacted at rest; this keeps
        API responses safe for older rows and explicit metadata."""

        return {
            "id": entry.id,
            "tenant_id": entry.tenant_id,
            "user_id": entry.user_id,
            "action": entry.action,
            "table_name": entry.table_name,
            "record_id": entry.record_id,
            "old_values": _redact_dict(entry.old_values),
            "new_values": _redact_dict(entry.new_values),
            "changed_fields": _redact_dict(entry.changed_fields),
            "ip_address": entry.ip_address,
            "user_agent": entry.user_agent,
            "metadata": _redact_dict(entry.metadata_json),
            "created_at": entry.created_at,
        }
