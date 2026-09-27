"""Private self-read and explicit tenant-wide staff pay management."""

from collections.abc import Callable, Coroutine
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from app.core.deps import CurrentUser, current_user, get_db, require_writable_tenant
from app.core.errors import AurumError, AuthorizationPolicyDeniedError, PermissionDeniedError
from app.core.logging import get_logger
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
from app.domains.staff_pay.service import StaffPayService
from app.middleware.error_handler import record_authorization_denial

logger = get_logger("staff_pay")


class _PrivateStaffPayRoute(APIRoute):
    """Keep private input out of validation responses, caches and exception logs."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError as exc:
                known = {
                    "employee_id",
                    "period_from",
                    "period_to",
                    "amount",
                    "note",
                    "operation_id",
                    "expected_row_version",
                    "kind",
                    "paid_on",
                    "reason",
                    "q",
                    "page",
                    "page_size",
                    "period_id",
                    "entry_id",
                }
                fields = sorted(
                    {part for error in exc.errors() for part in error["loc"] if part in known}
                )
                response = JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "validation_error",
                            "message": "Проверьте данные выплаты",
                            "details": {"fields": fields},
                        }
                    },
                )
            except AurumError as exc:
                logger.warning("staff_pay_domain_error", code=exc.code)
                if isinstance(exc, AuthorizationPolicyDeniedError):
                    try:
                        await record_authorization_denial(request, exc)
                    except Exception as audit_exc:
                        logger.error(
                            "staff_pay_denial_audit_failed", error_type=type(audit_exc).__name__
                        )
                response = JSONResponse(
                    status_code=exc.http_status,
                    content={
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        }
                    },
                )
            except HTTPException as exc:
                response = await http_exception_handler(request, exc)
            except Exception as exc:
                logger.error("staff_pay_request_failed", error_type=type(exc).__name__)
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "internal_error",
                            "message": "Internal server error",
                            "details": {},
                        }
                    },
                )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return safe_handler


router = APIRouter(
    prefix="/api/v1/staff-pay", tags=["staff-pay"], route_class=_PrivateStaffPayRoute
)


async def _member(
    response: Response, user: Annotated[CurrentUser, Depends(current_user)]
) -> CurrentUser:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    if (
        user.tenant_id is None
        or user.membership_status != "active"
        or user.is_developer
        or user.is_administrator
        or user.support_access_session_id is not None
    ):
        raise PermissionDeniedError("Выплаты доступны только сотрудникам аптеки")
    return user


async def _reader(user: Annotated[CurrentUser, Depends(_member)]) -> CurrentUser:
    if not (
        user.is_tenant_owner
        or user.has_tenant_scope("staff_pay.view")
        or user.has_tenant_scope("staff_pay.manage")
    ):
        raise PermissionDeniedError("Нет права просмотра выплат сотрудников")
    return user


async def _manager(
    user: Annotated[CurrentUser, Depends(_member)],
    writable: Annotated[CurrentUser, Depends(require_writable_tenant)],
) -> CurrentUser:
    if not (user.is_tenant_owner or user.has_tenant_scope("staff_pay.manage")):
        raise PermissionDeniedError("Нет права управления выплатами сотрудников")
    return user


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> StaffPayService:
    return StaffPayService(StaffPayRepository(db))


Member = Annotated[CurrentUser, Depends(_member)]
Reader = Annotated[CurrentUser, Depends(_reader)]
Manager = Annotated[CurrentUser, Depends(_manager)]
Service = Annotated[StaffPayService, Depends(_service)]
PageNumber = Annotated[int, Query(ge=1, le=100000)]
PageSize = Annotated[int, Query(ge=1, le=50)]


@router.get("/mine", response_model=Page[PeriodItem])
async def mine(
    user: Member, service: Service, page: PageNumber = 1, page_size: PageSize = 10
) -> Page[PeriodItem]:
    assert user.tenant_id is not None
    return await service.periods(user.tenant_id, user.user_id, page, page_size)


@router.get("/mine/{period_id}/entries", response_model=Page[EntryItem])
async def own_entries(
    period_id: UUID, user: Member, service: Service, page: PageNumber = 1, page_size: PageSize = 25
) -> Page[EntryItem] | Page[RevisionItem]:
    assert user.tenant_id is not None
    return await service.history(
        user.tenant_id, period_id, user.user_id, "entries", page, page_size
    )


@router.get("/mine/{period_id}/revisions", response_model=Page[RevisionItem])
async def own_revisions(
    period_id: UUID, user: Member, service: Service, page: PageNumber = 1, page_size: PageSize = 25
) -> Page[EntryItem] | Page[RevisionItem]:
    assert user.tenant_id is not None
    return await service.history(
        user.tenant_id, period_id, user.user_id, "revisions", page, page_size
    )


@router.post("/employees/search", response_model=Page[EmployeeItem])
async def employees(payload: EmployeeSearch, user: Reader, service: Service) -> Page[EmployeeItem]:
    assert user.tenant_id is not None
    return await service.employees(user.tenant_id, payload)


@router.get("", response_model=Page[PeriodItem])
async def periods(
    user: Reader,
    service: Service,
    employee_id: UUID | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 10,
) -> Page[PeriodItem]:
    assert user.tenant_id is not None
    return await service.periods(user.tenant_id, employee_id, page, page_size)


@router.get("/{period_id}/entries", response_model=Page[EntryItem])
async def entries(
    period_id: UUID, user: Reader, service: Service, page: PageNumber = 1, page_size: PageSize = 25
) -> Page[EntryItem] | Page[RevisionItem]:
    assert user.tenant_id is not None
    return await service.history(user.tenant_id, period_id, None, "entries", page, page_size)


@router.get("/{period_id}/revisions", response_model=Page[RevisionItem])
async def revisions(
    period_id: UUID, user: Reader, service: Service, page: PageNumber = 1, page_size: PageSize = 25
) -> Page[EntryItem] | Page[RevisionItem]:
    assert user.tenant_id is not None
    return await service.history(user.tenant_id, period_id, None, "revisions", page, page_size)


@router.post("", response_model=PeriodItem, status_code=201)
async def create(payload: PeriodCreate, user: Manager, service: Service) -> PeriodItem:
    assert user.tenant_id is not None
    return await service.create(user.tenant_id, payload)


@router.patch("/{period_id}", response_model=PeriodItem)
async def update(
    period_id: UUID, payload: PeriodUpdate, user: Manager, service: Service
) -> PeriodItem:
    assert user.tenant_id is not None
    return await service.update(user.tenant_id, period_id, payload)


@router.post("/{period_id}/entries", response_model=EntryItem, status_code=201)
async def add_entry(
    period_id: UUID, payload: EntryCreate, user: Manager, service: Service
) -> EntryItem:
    assert user.tenant_id is not None
    return await service.add_entry(user.tenant_id, period_id, payload)


@router.post("/{period_id}/entries/{entry_id}/reverse", response_model=EntryItem, status_code=201)
async def reverse(
    period_id: UUID, entry_id: UUID, payload: EntryReverse, user: Manager, service: Service
) -> EntryItem:
    assert user.tenant_id is not None
    return await service.reverse(user.tenant_id, period_id, entry_id, payload)
