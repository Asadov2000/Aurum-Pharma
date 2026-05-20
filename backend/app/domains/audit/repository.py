"""DB access for the audit domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_entry(self, **fields: Any) -> AuditLog:
        entry = AuditLog(**fields)
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def search(
        self,
        *,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        table_name: str | None = None,
        record_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
        global_scope: bool = False,
    ) -> tuple[list[AuditLog], int]:
        """Returns (items, total).

        - `global_scope=True` skips the tenant_id filter; the caller must
          already have verified `audit.view.global` (developer-only).
        - Otherwise the caller passes `tenant_id` to bind the query.
        """
        clauses: list[Any] = []
        if not global_scope and tenant_id is not None:
            clauses.append(AuditLog.tenant_id == tenant_id)
        elif global_scope and tenant_id is not None:
            clauses.append(AuditLog.tenant_id == tenant_id)
        if user_id is not None:
            clauses.append(AuditLog.user_id == user_id)
        if action:
            clauses.append(AuditLog.action == action)
        if table_name:
            clauses.append(AuditLog.table_name == table_name)
        if record_id is not None:
            clauses.append(AuditLog.record_id == record_id)
        if date_from is not None:
            clauses.append(AuditLog.created_at >= date_from)
        if date_to is not None:
            clauses.append(AuditLog.created_at <= date_to)

        list_stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)
        if clauses:
            list_stmt = list_stmt.where(and_(*clauses))
            count_stmt = count_stmt.where(and_(*clauses))
        list_stmt = (
            list_stmt.order_by(AuditLog.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await self.session.execute(list_stmt)
        items = list(result.scalars().all())
        total = int((await self.session.execute(count_stmt)).scalar_one())
        return items, total
