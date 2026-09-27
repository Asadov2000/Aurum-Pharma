"""Scoped read projections and the narrow database command boundary."""

import json
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

PERIOD_SELECT = """
SELECT p.id,p.employee_id,p.period_from,p.period_to,p.amount,p.note,p.row_version,
 p.created_at,p.updated_at,COALESCE(t.paid_amount,0) AS paid_amount,
 COALESCE(t.advance_amount,0) AS advance_amount,
 p.amount-COALESCE(t.paid_amount,0) AS remaining_amount
FROM staff_pay_period p LEFT JOIN LATERAL (
 SELECT sum(e.amount) AS paid_amount,
   COALESCE(sum(e.amount) FILTER(WHERE e.kind='advance'),0) AS advance_amount
 FROM staff_pay_entry e WHERE e.tenant_id=p.tenant_id AND e.period_id=p.id
   AND e.kind<>'reversal' AND NOT EXISTS(SELECT 1 FROM staff_pay_entry r
     WHERE r.tenant_id=e.tenant_id AND r.reverses_entry_id=e.id)
) t ON true
"""


class StaffPayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def timezone(self, tenant_id: UUID) -> str:
        value = await self.session.scalar(
            text("SELECT report_timezone FROM tenant_settings WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        return str(value or "Asia/Dushanbe")

    async def period(
        self, tenant_id: UUID, period_id: UUID, employee_id: UUID | None = None
    ) -> RowMapping | None:
        result = await self.session.execute(
            text(
                PERIOD_SELECT + " WHERE p.tenant_id=:tenant AND p.id=:id"
                " AND (CAST(:employee AS UUID) IS NULL OR p.employee_id=:employee)"
            ),
            {"tenant": tenant_id, "id": period_id, "employee": employee_id},
        )
        return result.mappings().one_or_none()

    async def periods(
        self, tenant_id: UUID, employee_id: UUID | None, page: int, page_size: int
    ) -> tuple[list[RowMapping], int]:
        params = {
            "tenant": tenant_id,
            "employee": employee_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = (
            " WHERE p.tenant_id=:tenant AND (CAST(:employee AS UUID) IS NULL"
            " OR p.employee_id=:employee)"
        )
        total = await self.session.scalar(
            text("SELECT count(*) FROM staff_pay_period p" + where), params
        )
        result = await self.session.execute(
            text(
                PERIOD_SELECT
                + where
                + " ORDER BY p.period_from DESC,p.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        return list(result.mappings()), int(total or 0)

    async def history(
        self,
        tenant_id: UUID,
        period_id: UUID,
        kind: Literal["entries", "revisions"],
        page: int,
        page_size: int,
    ) -> tuple[list[RowMapping], int]:
        table = "staff_pay_entry" if kind == "entries" else "staff_pay_revision"
        projection = "e.id,e.amount,e.note,e.created_at,"
        if kind == "entries":
            projection += (
                "e.kind,e.paid_on,e.reverses_entry_id,EXISTS(SELECT 1"
                " FROM staff_pay_entry r WHERE r.tenant_id=e.tenant_id"
                " AND r.reverses_entry_id=e.id) AS is_reversed"
            )
        else:
            projection += "e.row_version"
        params = {
            "tenant": tenant_id,
            "period": period_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = " WHERE e.tenant_id=:tenant AND e.period_id=:period"
        total = await self.session.scalar(text(f"SELECT count(*) FROM {table} e" + where), params)
        result = await self.session.execute(
            text(
                f"SELECT {projection} FROM {table} e"
                + where
                + " ORDER BY e.created_at DESC,e.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        return list(result.mappings()), int(total or 0)

    async def entry(self, tenant_id: UUID, entry_id: UUID) -> RowMapping:
        result = await self.session.execute(
            text("""
          SELECT e.id,e.kind,e.amount,e.paid_on,e.note,e.reverses_entry_id,e.created_at,
           EXISTS(SELECT 1 FROM staff_pay_entry r WHERE r.tenant_id=e.tenant_id
             AND r.reverses_entry_id=e.id) AS is_reversed
          FROM staff_pay_entry e WHERE e.tenant_id=:tenant AND e.id=:id
        """),
            {"tenant": tenant_id, "id": entry_id},
        )
        return result.mappings().one()

    async def employees(
        self, tenant_id: UUID, q: str, page: int, page_size: int
    ) -> tuple[list[RowMapping], int]:
        # Escape LIKE metacharacters; names never appear in URLs or logs.
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params = {
            "tenant": tenant_id,
            "q": pattern,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = " WHERE m.full_name ILIKE :q"
        source = " FROM public.staff_pay_employees(:tenant) m"
        total = await self.session.scalar(text("SELECT count(*)" + source + where), params)
        result = await self.session.execute(
            text(
                "SELECT m.id,m.full_name,m.status"
                + source
                + where
                + " ORDER BY m.full_name,m.id LIMIT :limit OFFSET :offset"
            ),
            params,
        )
        return list(result.mappings()), int(total or 0)

    async def command(
        self,
        action: str,
        payload: dict[str, object],
        period_id: UUID | None = None,
        entry_id: UUID | None = None,
        operation_id: UUID | None = None,
    ) -> UUID:
        value = await self.session.scalar(
            text("""
          SELECT public.command_staff_pay(:action,:period,:entry,:operation,CAST(:payload AS JSONB))
        """),
            {
                "action": action,
                "period": period_id,
                "entry": entry_id,
                "operation": operation_id,
                "payload": json.dumps(payload, sort_keys=True),
            },
        )
        return cast(UUID, value)
