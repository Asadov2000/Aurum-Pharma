"""Manual periods and payments; no attendance, sales or cash ledger integration."""

from typing import Literal
from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domains.staff_pay.repository import StaffPayRepository
from app.domains.staff_pay.schemas import (
    EmployeeItem,
    EmployeeSearch,
    EntryCreate,
    EntryItem,
    EntryReverse,
    Page,
    PeriodCreate,
    PeriodItem,
    PeriodUpdate,
    RevisionItem,
)


class StaffPayService:
    def __init__(self, repository: StaffPayRepository) -> None:
        self.repository = repository

    async def periods(
        self, tenant_id: UUID, employee_id: UUID | None, page: int, page_size: int
    ) -> Page[PeriodItem]:
        rows, total = await self.repository.periods(tenant_id, employee_id, page, page_size)
        return Page[PeriodItem](
            items=[PeriodItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            timezone=await self.repository.timezone(tenant_id),
        )

    async def history(
        self,
        tenant_id: UUID,
        period_id: UUID,
        employee_id: UUID | None,
        kind: Literal["entries", "revisions"],
        page: int,
        page_size: int,
    ) -> Page[EntryItem] | Page[RevisionItem]:
        if await self.repository.period(tenant_id, period_id, employee_id) is None:
            raise NotFoundError("Период выплат не найден")
        rows, total = await self.repository.history(tenant_id, period_id, kind, page, page_size)
        timezone = await self.repository.timezone(tenant_id)
        if kind == "entries":
            return Page[EntryItem](
                items=[EntryItem.model_validate(row) for row in rows],
                total=total,
                page=page,
                page_size=page_size,
                timezone=timezone,
            )
        return Page[RevisionItem](
            items=[RevisionItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            timezone=timezone,
        )

    async def employees(self, tenant_id: UUID, payload: EmployeeSearch) -> Page[EmployeeItem]:
        rows, total = await self.repository.employees(
            tenant_id, payload.q, payload.page, payload.page_size
        )
        return Page[EmployeeItem](
            items=[EmployeeItem.model_validate(row) for row in rows],
            total=total,
            page=payload.page,
            page_size=payload.page_size,
            timezone=await self.repository.timezone(tenant_id),
        )

    async def _command(
        self,
        action: str,
        payload: dict[str, object],
        period_id: UUID | None = None,
        entry_id: UUID | None = None,
        operation_id: UUID | None = None,
    ) -> UUID:
        try:
            async with self.repository.session.begin_nested():
                return await self.repository.command(
                    action, payload, period_id, entry_id, operation_id
                )
        except DBAPIError as exc:
            state = getattr(exc.orig, "sqlstate", None)
            if state == "42501":
                raise PermissionDeniedError("Выплаты недоступны или изменения ограничены") from None
            if state == "P0002":
                raise NotFoundError("Сотрудник, период или выплата не найдены") from None
            if state in {"23505", "40001"}:
                raise ConflictError(
                    "Запись уже изменена, период пересекается или операция повторена "
                    "с другими данными. Обновите страницу."
                ) from None
            if state in {"22023", "23514", "22007", "22008", "22P02"}:
                raise ValidationError("Проверьте сумму, период и дату выплаты") from None
            raise

    async def create(self, tenant_id: UUID, payload: PeriodCreate) -> PeriodItem:
        data = payload.model_dump(mode="json", exclude={"operation_id"})
        data["amount"] = format(payload.amount, ".2f")
        identifier = await self._command("create", data, operation_id=payload.operation_id)
        return PeriodItem.model_validate(await self.repository.period(tenant_id, identifier))

    async def update(self, tenant_id: UUID, period_id: UUID, payload: PeriodUpdate) -> PeriodItem:
        data = payload.model_dump(mode="json")
        data["amount"] = format(payload.amount, ".2f")
        await self._command("update", data, period_id=period_id)
        return PeriodItem.model_validate(await self.repository.period(tenant_id, period_id))

    async def add_entry(self, tenant_id: UUID, period_id: UUID, payload: EntryCreate) -> EntryItem:
        data = payload.model_dump(mode="json", exclude={"operation_id"})
        data["amount"] = format(payload.amount, ".2f")
        identifier = await self._command(
            "entry", data, period_id=period_id, operation_id=payload.operation_id
        )
        return EntryItem.model_validate(await self.repository.entry(tenant_id, identifier))

    async def reverse(
        self, tenant_id: UUID, period_id: UUID, entry_id: UUID, payload: EntryReverse
    ) -> EntryItem:
        identifier = await self._command(
            "reverse",
            {"reason": payload.reason},
            period_id=period_id,
            entry_id=entry_id,
            operation_id=payload.operation_id,
        )
        return EntryItem.model_validate(await self.repository.entry(tenant_id, identifier))
