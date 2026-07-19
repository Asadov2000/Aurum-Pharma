"""DB access for the audit domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, and_, bindparam, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_entry(
        self,
        *,
        tenant_id: UUID | None,
        user_id: UUID | None,
        action: str,
        table_name: str,
        record_id: UUID | None,
        metadata_json: dict[str, Any] | None,
    ) -> AuditLog:
        entry_id = (
            await self.session.execute(
                select(
                    func.append_audit_event(
                        bindparam(
                            "audit_tenant_id",
                            tenant_id,
                            type_=PG_UUID(as_uuid=True),
                        ),
                        bindparam(
                            "audit_user_id",
                            user_id,
                            type_=PG_UUID(as_uuid=True),
                        ),
                        bindparam("audit_action", action, type_=Text()),
                        bindparam("audit_table_name", table_name, type_=Text()),
                        bindparam(
                            "audit_record_id",
                            record_id,
                            type_=PG_UUID(as_uuid=True),
                        ),
                        bindparam(
                            "audit_metadata",
                            metadata_json,
                            type_=JSONB(),
                        ),
                        type_=PG_UUID(as_uuid=True),
                    )
                )
            )
        ).scalar_one()
        entry = await self.session.get(AuditLog, entry_id)
        if entry is None:
            raise RuntimeError("Appended audit event is not visible in its tenant context")
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
        date_to_exclusive: datetime | None = None,
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
        if date_to_exclusive is not None:
            clauses.append(AuditLog.created_at < date_to_exclusive)

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
