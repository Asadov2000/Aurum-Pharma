"""Database access for the foundation domain. No business logic here."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.foundation.models import Branch, Register, Tenant, TenantSettings


class FoundationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # tenant
    # -------------------------------------------------------------------------

    async def create_tenant(self, **fields: Any) -> Tenant:
        t = Tenant(**fields)
        self.session.add(t)
        await self.session.flush()
        await self.session.refresh(t)
        return t

    async def list_tenants(self, *, limit: int = 100, offset: int = 0) -> list[Tenant]:
        stmt = select(Tenant).order_by(Tenant.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_tenant(self, tenant_id: UUID) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def update_tenant(self, tenant: Tenant, **fields: Any) -> Tenant:
        for key, value in fields.items():
            setattr(tenant, key, value)
        await self.session.flush()
        await self.session.refresh(tenant)
        return tenant

    # -------------------------------------------------------------------------
    # tenant_settings
    # -------------------------------------------------------------------------

    async def create_default_settings(self, tenant_id: UUID) -> TenantSettings:
        s = TenantSettings(
            tenant_id=tenant_id,
            expiry_thresholds={"yellow": 6, "orange": 3, "red": 1},
            expired_sale_mode="strict",
            refund_reason_mode="optional",
            session_admin_minutes=480,
            session_pos_minutes=480,
            pin_mode_enabled=False,
            pos_payment_methods=["cash", "card", "qr"],
            pos_mixed_payment_enabled=True,
            prescription_warning_text=(
                "Отпуск рецептурных препаратов осуществляется в соответствии "
                "с действующим законодательством РТ"
            ),
        )
        self.session.add(s)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def get_settings(self, tenant_id: UUID) -> TenantSettings | None:
        statement = (
            select(TenantSettings)
            .where(TenantSettings.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_settings_for_pos(self, tenant_id: UUID) -> TenantSettings | None:
        stmt = (
            select(TenantSettings)
            .where(TenantSettings.tenant_id == tenant_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_settings(self, settings: TenantSettings, **fields: Any) -> TenantSettings:
        for key, value in fields.items():
            setattr(settings, key, value)
        settings.version += 1
        await self.session.flush()
        await self.session.refresh(settings)
        return settings

    async def update_settings_if_version(
        self,
        *,
        tenant_id: UUID,
        expected_version: int,
        fields: dict[str, object],
    ) -> TenantSettings | None:
        result = await self.session.execute(
            update(TenantSettings)
            .where(
                TenantSettings.tenant_id == tenant_id,
                TenantSettings.version == expected_version,
            )
            .values(**fields, version=TenantSettings.version + 1)
            .returning(TenantSettings)
        )
        return result.scalar_one_or_none()

    # -------------------------------------------------------------------------
    # branch
    # -------------------------------------------------------------------------

    async def create_branch(self, **fields: Any) -> Branch:
        b = Branch(**fields)
        self.session.add(b)
        await self.session.flush()
        await self.session.refresh(b)
        return b

    async def list_branches(self, *, include_inactive: bool = False) -> list[Branch]:
        stmt = select(Branch)
        if not include_inactive:
            stmt = stmt.where(Branch.is_active.is_(True))
        stmt = stmt.order_by(Branch.created_at.asc(), Branch.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_branches(
        self,
        *,
        tenant_id: UUID,
        q: str | None = None,
        branch_type: str | None = None,
        is_active: bool | None = None,
        allowed_branch_ids: set[UUID] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Branch], int]:
        if allowed_branch_ids is not None and not allowed_branch_ids:
            return [], 0

        clauses: list[ColumnElement[bool]] = [Branch.tenant_id == tenant_id]
        term = q.strip() if q is not None else ""
        if term:
            clauses.append(
                or_(
                    Branch.name.icontains(term, autoescape=True),
                    Branch.address.icontains(term, autoescape=True),
                    Branch.license_number.icontains(term, autoescape=True),
                )
            )
        if branch_type is not None:
            clauses.append(Branch.branch_type == branch_type)
        if is_active is not None:
            clauses.append(Branch.is_active.is_(is_active))
        if allowed_branch_ids is not None:
            clauses.append(Branch.id.in_(sorted(allowed_branch_ids, key=str)))

        count_stmt = select(func.count()).select_from(Branch).where(*clauses)
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = (
            select(Branch)
            .where(*clauses)
            .order_by(func.lower(Branch.name).asc(), Branch.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_branch(self, branch_id: UUID) -> Branch | None:
        return await self.session.get(Branch, branch_id)

    async def get_branch_for_update(self, branch_id: UUID) -> Branch | None:
        stmt = select(Branch).where(Branch.id == branch_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_branch(self, branch: Branch, **fields: Any) -> Branch:
        for key, value in fields.items():
            setattr(branch, key, value)
        await self.session.flush()
        await self.session.refresh(branch)
        return branch

    async def count_active_branches(self, tenant_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Branch)
            .where(and_(Branch.tenant_id == tenant_id, Branch.is_active.is_(True)))
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def has_open_shift_for_branch(self, branch_id: UUID) -> bool:
        result = await self.session.execute(
            text("SELECT 1 FROM shift WHERE branch_id = :branch_id AND status = 'open' LIMIT 1"),
            {"branch_id": str(branch_id)},
        )
        return result.scalar_one_or_none() is not None

    # -------------------------------------------------------------------------
    # register
    # -------------------------------------------------------------------------

    async def create_register(self, **fields: Any) -> Register:
        r = Register(**fields)
        self.session.add(r)
        await self.session.flush()
        await self.session.refresh(r)
        return r

    async def list_registers(
        self, *, branch_id: UUID | None = None, include_inactive: bool = False
    ) -> list[Register]:
        stmt = select(Register)
        if branch_id is not None:
            stmt = stmt.where(Register.branch_id == branch_id)
        if not include_inactive:
            stmt = stmt.where(Register.is_active.is_(True))
        stmt = stmt.order_by(Register.created_at.asc(), Register.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search_registers(
        self,
        *,
        tenant_id: UUID,
        q: str | None = None,
        branch_id: UUID | None = None,
        printer_type: str | None = None,
        is_active: bool | None = None,
        allowed_branch_ids: set[UUID] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[Register], int]:
        if allowed_branch_ids is not None:
            if not allowed_branch_ids:
                return [], 0
            if branch_id is not None and branch_id not in allowed_branch_ids:
                return [], 0

        clauses: list[ColumnElement[bool]] = [Register.tenant_id == tenant_id]
        term = q.strip() if q is not None else ""
        if term:
            clauses.append(Register.name.icontains(term, autoescape=True))
        if branch_id is not None:
            clauses.append(Register.branch_id == branch_id)
        if printer_type is not None:
            clauses.append(Register.printer_type == printer_type)
        if is_active is not None:
            clauses.append(Register.is_active.is_(is_active))
        if allowed_branch_ids is not None:
            clauses.append(Register.branch_id.in_(sorted(allowed_branch_ids, key=str)))

        count_stmt = select(func.count()).select_from(Register).where(*clauses)
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = (
            select(Register)
            .where(*clauses)
            .order_by(func.lower(Register.name).asc(), Register.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_register(self, register_id: UUID) -> Register | None:
        return await self.session.get(Register, register_id)

    async def get_register_for_update(self, register_id: UUID) -> Register | None:
        stmt = select(Register).where(Register.id == register_id).with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_register(self, register: Register, **fields: Any) -> Register:
        for key, value in fields.items():
            setattr(register, key, value)
        await self.session.flush()
        await self.session.refresh(register)
        return register

    async def has_open_shift_for_register(self, register_id: UUID) -> bool:
        result = await self.session.execute(
            text(
                "SELECT 1 FROM shift WHERE register_id = :register_id AND status = 'open' LIMIT 1"
            ),
            {"register_id": str(register_id)},
        )
        return result.scalar_one_or_none() is not None
