"""Deterministic, transaction-safe showcase dataset generator."""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.selectable import FromClause

from app.core.security import hash_password
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.billing.models import (
    Invoice,
    Payment,
    SubscriptionPlan,
    TenantSubscription,
)
from app.domains.catalog.models import Barcode, MasterCatalog, TenantCatalog
from app.domains.foundation.models import Branch, Register, Tenant, TenantSettings
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.inventory.models import Batch, BatchMovement, WriteOff
from app.domains.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationSubscription,
)
from app.domains.onboarding.models import OnboardingChecklist, WizardState
from app.domains.pos.models import PrescriptionLog, Sale, SaleItem, SalePayment, Shift
from app.domains.roles.models import (
    Role,
    RolePermission,
    TenantMembership,
    UserAssignment,
)
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.domains.suppliers.models import Supplier, SupplierReturn
from app.showcase.catalog import CatalogSeedRow, showcase_catalog_rows
from app.showcase.profiles import ShowcaseProfile

MONEY = Decimal("0.01")
QTY = Decimal("0.001")
SHOWCASE_NAMESPACE = UUID("95947fc5-29b0-4fc5-9be8-75580c7f5ba4")
LOCAL_TIMEZONE = ZoneInfo("Asia/Dushanbe")
SHOWCASE_TENANT_LEGAL_NAME = "ООО «Аурум Фарма Демо»"
EMPLOYEE_PASSWORD = "DemoUser1234"

Row = dict[str, object]


@dataclass(frozen=True, slots=True)
class BranchDefinition:
    name: str
    address: str
    branch_type: str
    license_number: str


@dataclass(frozen=True, slots=True)
class BranchRecord:
    id: UUID
    definition: BranchDefinition
    index: int


@dataclass(frozen=True, slots=True)
class RegisterRecord:
    id: UUID
    branch_id: UUID
    branch_index: int
    code: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    key: str
    name: str
    description: str
    level: int
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmployeeDefinition:
    key: str
    full_name: str
    role_key: str
    branch_index: int | None
    active: bool = True


@dataclass(frozen=True, slots=True)
class EmployeeRecord:
    user_id: UUID
    membership_id: UUID
    role_key: str
    branch_id: UUID | None
    active: bool


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    id: UUID
    seed: CatalogSeedRow
    demand_weight: float


@dataclass(slots=True)
class BatchStock:
    id: UUID
    branch_id: UUID
    catalog: CatalogRecord
    supplier_id: UUID
    incoming_document_id: UUID
    received_at: datetime
    expires_at: date
    purchase_price: Decimal
    sale_price: Decimal
    qty_initial: Decimal
    qty_remaining: Decimal


@dataclass(slots=True)
class SaleItemPlan:
    id: UUID
    catalog_id: UUID
    batch_id: UUID
    qty: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    total_price: Decimal
    parent_sale_item_id: UUID | None = None


@dataclass(slots=True)
class PaymentPlan:
    id: UUID
    method: str
    amount: Decimal


@dataclass(slots=True)
class SalePlan:
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    cashier_user_id: UUID
    occurred_at: datetime
    sale_type: str
    status: str
    items: list[SaleItemPlan]
    payments: list[PaymentPlan]
    parent_sale_id: UUID | None = None
    receipt_seq: int | None = None
    receipt_number: str | None = None
    voided_at: datetime | None = None
    voided_by_sale_id: UUID | None = None
    prescriptions: list[UUID] = field(default_factory=list)

    @property
    def total_amount(self) -> Decimal:
        return _money(sum((item.total_price for item in self.items), Decimal("0")))


@dataclass(frozen=True, slots=True)
class ShiftRecord:
    id: UUID
    register_id: UUID
    branch_id: UUID
    local_date: date
    opened_by_user_id: UUID


@dataclass(frozen=True, slots=True)
class ShowcaseSummary:
    tenant_id: UUID
    branches: int
    registers: int
    employees: int
    catalog_items: int
    incoming_documents: int
    batches: int
    sales: int
    returns: int
    write_offs: int
    supplier_returns: int


_BRANCHES = (
    BranchDefinition(
        name="Аптека «Сино» — центр",
        address="г. Душанбе, проспект Рудаки, 58",
        branch_type="pharmacy",
        license_number="DEMO-TJ-PH-0001",
    ),
    BranchDefinition(
        name="Аптека «Сино» — Фирдавси",
        address="г. Душанбе, ул. Н. Карабаева, 42",
        branch_type="pharmacy",
        license_number="DEMO-TJ-PH-0002",
    ),
    BranchDefinition(
        name="Аптека «Сино» — Худжанд",
        address="г. Худжанд, проспект Исмоили Сомони, 116",
        branch_type="pharmacy",
        license_number="DEMO-TJ-PH-0003",
    ),
    BranchDefinition(
        name="Аптечный пункт «Сино» — Бохтар",
        address="г. Бохтар, ул. Носири Хусрав, 18",
        branch_type="pharmacy_post",
        license_number="DEMO-TJ-PP-0004",
    ),
    BranchDefinition(
        name="Аптека «Сино» — Куляб",
        address="г. Куляб, ул. С. Сафарова, 27",
        branch_type="pharmacy",
        license_number="DEMO-TJ-PH-0005",
    ),
    BranchDefinition(
        name="Аптечный пункт «Сино» — Турсунзаде",
        address="г. Турсунзаде, ул. И. Сомони, 9",
        branch_type="pharmacy_post",
        license_number="DEMO-TJ-PP-0006",
    ),
)

_ROLE_DEFINITIONS = (
    RoleDefinition(
        key="manager",
        name="Управляющий аптечной сетью",
        description="Операционное управление без доступа к платформенным функциям Aurum Pharma.",
        level=3,
        permissions=(
            "audit.view.tenant",
            "batches.create",
            "batches.update",
            "batches.view",
            "batches.write_off",
            "branches.create",
            "branches.update",
            "branches.view",
            "catalog.create",
            "catalog.delete",
            "catalog.update",
            "catalog.view",
            "incoming.create",
            "incoming.return",
            "incoming.view",
            "pos.handle_prescription",
            "pos.refund",
            "pos.sell",
            "pos.shift_close",
            "pos.shift_open",
            "registers.create",
            "registers.update",
            "registers.view",
            "reports.export",
            "reports.view",
            "sales.view.tenant",
            "settings.update",
            "suppliers.create",
            "suppliers.update",
            "suppliers.view",
            "tenant.view",
            "users.view",
        ),
    ),
    RoleDefinition(
        key="warehouse",
        name="Заведующий складом",
        description="Приходы, партии, поставщики, сроки годности и складские операции.",
        level=3,
        permissions=(
            "batches.create",
            "batches.update",
            "batches.view",
            "batches.write_off",
            "catalog.create",
            "catalog.update",
            "catalog.view",
            "incoming.create",
            "incoming.return",
            "incoming.view",
            "reports.export",
            "reports.view",
            "suppliers.create",
            "suppliers.update",
            "suppliers.view",
        ),
    ),
    RoleDefinition(
        key="senior_pharmacist",
        name="Старший фармацевт",
        description="Продажи, возвраты, рецептурный отпуск и контроль смены своего филиала.",
        level=3,
        permissions=(
            "audit.view.own",
            "batches.view",
            "catalog.view",
            "incoming.view",
            "pos.handle_prescription",
            "pos.refund",
            "pos.sell",
            "pos.shift_close",
            "pos.shift_open",
            "reports.view",
            "sales.view.tenant",
        ),
    ),
    RoleDefinition(
        key="cashier",
        name="Фармацевт-кассир",
        description="Кассовая смена, продажи и разрешённые возвраты в назначенном филиале.",
        level=4,
        permissions=(
            "audit.view.own",
            "batches.view",
            "catalog.view",
            "pos.handle_prescription",
            "pos.refund",
            "pos.sell",
            "pos.shift_close",
            "pos.shift_open",
            "sales.view.own",
        ),
    ),
    RoleDefinition(
        key="analyst",
        name="Финансовый аналитик",
        description="Просмотр показателей сети и экспорт отчётов без права менять операции.",
        level=3,
        permissions=(
            "audit.view.tenant",
            "batches.view",
            "branches.view",
            "catalog.view",
            "incoming.view",
            "registers.view",
            "reports.export",
            "reports.view",
            "sales.view.tenant",
            "suppliers.view",
            "tenant.view",
        ),
    ),
)

_EMPLOYEES = (
    EmployeeDefinition("manager", "Мехрона Саидова", "manager", None),
    EmployeeDefinition("warehouse-1", "Фаридун Набиев", "warehouse", 0),
    EmployeeDefinition("warehouse-2", "Зарина Мирзоева", "warehouse", 1),
    EmployeeDefinition("senior-1", "Манижа Каримова", "senior_pharmacist", 0),
    EmployeeDefinition("senior-2", "Далер Усмонов", "senior_pharmacist", 1),
    EmployeeDefinition("senior-3", "Нигина Холова", "senior_pharmacist", 2),
    EmployeeDefinition("cashier-1", "Шабнам Рахимова", "cashier", 0),
    EmployeeDefinition("cashier-2", "Комрон Давлатов", "cashier", 0),
    EmployeeDefinition("cashier-3", "Муниса Шарифова", "cashier", 1),
    EmployeeDefinition("cashier-4", "Рустам Олимов", "cashier", 1),
    EmployeeDefinition("cashier-5", "Мадина Юсуфова", "cashier", 2),
    EmployeeDefinition("cashier-6", "Сорбон Ахмедов", "cashier", 2),
    EmployeeDefinition("analyst", "Искандар Хакимов", "analyst", None),
    EmployeeDefinition("former", "Сотрудник Архивный", "cashier", 0, active=False),
)

_SUPPLIERS = (
    ("Фарм Дистрибьюшн Демо", "Оптовые поставки лекарственных средств"),
    ("Сино Мед Демо", "Лекарственные средства и медицинские изделия"),
    ("Ориён Фарм Демо", "Национальная фармацевтическая дистрибуция"),
    ("Сомон Медикал Демо", "Медицинские изделия и расходные материалы"),
    ("Восток Фарма Демо", "Импортные лекарственные средства"),
    ("Памир Логистик Демо", "Холодовая цепь и специальные поставки"),
    ("Мехр Гигиена Демо", "Гигиена и товары для ухода"),
    ("Авиценна Опт Демо", "Аптечный ассортимент широкого профиля"),
    ("Тиб Техника Демо", "Диагностическая и медицинская техника"),
    ("Здоровье Плюс Демо", "Товары для матери и ребёнка"),
    ("Фарма Резерв Демо", "Резервный поставщик лекарств"),
    ("Шифо Сервис Демо", "Локальные поставки аптечных товаров"),
)

_EXTRA_TENANTS = (
    ("Аптека «Шифо» (демо)", "active"),
    ("Сеть «Дармон» (демо)", "trial"),
    ("Аптека «Саломат» (демо)", "active"),
    ("МедФарм Север (демо)", "grace_period"),
    ("Аптечный дом «Вафо» (демо)", "readonly"),
    ("Фарм Плюс Бохтар (демо)", "active"),
    ("Аптека «Зиндаги» (демо)", "setup"),
    ("Сеть «Ориён Тиб» (демо)", "active"),
    ("Аптека «Рахмат» (демо)", "archived"),
    ("ФармСервис Куляб (демо)", "trial"),
    ("Аптечный пункт «Мадад» (демо)", "active"),
    ("Сеть «Сифат» (демо)", "readonly"),
    ("Аптека «Умед» (демо)", "active"),
    ("Медика Истаравшан (демо)", "grace_period"),
    ("Аптека «Нур» (демо)", "active"),
    ("ФармМарказ (демо)", "trial"),
    ("Аптека «Сабо» (демо)", "active"),
    ("Дармон Плюс (демо)", "active"),
    ("Аптека «Бехбуд» (демо)", "setup"),
    ("Тибби Нав (демо)", "active"),
)


def _uuid(key: str) -> UUID:
    return uuid5(SHOWCASE_NAMESPACE, key)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _qty(value: Decimal) -> Decimal:
    return value.quantize(QTY, rounding=ROUND_HALF_UP)


def _utc_at(local_date: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime.combine(
        local_date,
        time(hour=hour, minute=minute, second=second),
        tzinfo=LOCAL_TIMEZONE,
    ).astimezone(UTC)


def _operation_hash(kind: str, operation_id: UUID) -> str:
    return hashlib.sha256(f"aurum-showcase:{kind}:{operation_id}".encode()).hexdigest()


def _ean13(sequence: int) -> str:
    body = f"299{sequence:09d}"
    checksum_total = sum(
        int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body)
    )
    return f"{body}{(10 - checksum_total % 10) % 10}"


async def _bulk_insert(
    session: AsyncSession,
    table: FromClause,
    rows: Sequence[Row],
    *,
    chunk_size: int = 2_000,
) -> None:
    insert_table = cast(Table, table)
    for offset in range(0, len(rows), chunk_size):
        await session.execute(
            insert_table.insert(),
            list(rows[offset : offset + chunk_size]),
        )


def _catalog_weight(row: CatalogSeedRow, index: int) -> float:
    if row.master_linked:
        base = 11.0 if index < 80 else 6.0
        if row.dispensing_type == "prescription":
            base *= 0.72
        if row.storage_type != "normal":
            base *= 0.28
        return base
    if row.category in {"Гигиена", "Товары для детей", "Перевязочные материалы"}:
        return 3.5
    if row.category == "Медицинская техника":
        return 0.35
    return 1.6


def _working_hours() -> dict[str, dict[str, object]]:
    return {
        day: {"open": "08:00", "close": "22:00", "is_closed": False}
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }


async def prepare_main_tenant(
    session: AsyncSession,
    *,
    profile: ShowcaseProfile,
    today: date,
) -> tuple[Tenant, AppUser]:
    owner = await session.scalar(select(AppUser).where(AppUser.email_lower == "owner@aurum.tj"))
    if owner is None or owner.home_tenant_id is None:
        raise RuntimeError("Showcase base owner account is missing its home tenant")
    tenant = await session.get(Tenant, owner.home_tenant_id)
    if tenant is None:
        raise RuntimeError("Showcase base tenant is missing")

    started_at = _utc_at(today - timedelta(days=profile.history_days + 70), 9)
    tenant.name = "Аптечная сеть «Сино»"
    tenant.legal_name = SHOWCASE_TENANT_LEGAL_NAME
    tenant.inn_or_tin = "DEMO-TIN-020000000"
    tenant.registration_number = "DEMO-REG-TJ-2026-0001"
    tenant.contact_email = "office@showcase.aurum.invalid"
    tenant.contact_phone = "+992 00 000 00 01"
    tenant.legal_address = "г. Душанбе, проспект Рудаки, 58"
    tenant.status = "active"
    tenant.setup_started_at = started_at
    tenant.trial_started_at = started_at + timedelta(days=3)
    tenant.trial_ends_at = started_at + timedelta(days=33)
    tenant.drug_catalog_mode = "autonomous"
    tenant.updated_at = utc_now()

    settings = await session.get(TenantSettings, tenant.id)
    if settings is None:
        raise RuntimeError("Showcase tenant settings are missing")
    settings.expiry_thresholds = {"yellow": 6, "orange": 3, "red": 1}
    settings.expired_sale_mode = "strict"
    settings.refund_reason_mode = "required"
    settings.session_admin_minutes = 240
    settings.session_pos_minutes = 480
    settings.pin_mode_enabled = False
    settings.draft_sale_lifetime_min = 30
    settings.report_timezone = "Asia/Dushanbe"
    settings.prescription_warning_text = (
        "Проверьте рецепт, дозировку и данные отпуска перед завершением продажи."
    )
    settings.updated_by = owner.id

    wizard = await session.get(WizardState, tenant.id)
    if wizard is not None:
        wizard.current_step = 8
        wizard.steps_completed = list(range(1, 9))
        wizard.wizard_data = {
            "company": {"completed": True},
            "branches": profile.branches,
            "catalog_mode": "autonomous",
        }
        wizard.is_completed = True
        wizard.started_at = started_at
        wizard.completed_at = started_at + timedelta(days=2)

    checklist = await session.get(OnboardingChecklist, tenant.id)
    if checklist is not None:
        checklist.completed_tasks = [
            "company_profile",
            "first_branch",
            "first_register",
            "catalog_import",
            "first_employee",
            "first_incoming",
            "first_sale",
        ]
        checklist.trial_eligible = True
        checklist.trial_started_at = tenant.trial_started_at
        checklist.setup_ends_at = started_at + timedelta(days=14)

    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(owner.id)},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant.id)},
    )
    await session.flush()
    return tenant, owner


async def seed_branches_and_registers(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    profile: ShowcaseProfile,
    today: date,
) -> tuple[list[BranchRecord], list[RegisterRecord]]:
    definitions = _BRANCHES[: profile.branches]
    branches = [
        BranchRecord(
            id=_uuid(f"branch:{index}"),
            definition=definition,
            index=index,
        )
        for index, definition in enumerate(definitions)
    ]
    branch_rows: list[Row] = []
    for branch in branches:
        branch_rows.append(
            {
                "id": branch.id,
                "tenant_id": tenant_id,
                "name": branch.definition.name,
                "address": branch.definition.address,
                "branch_type": branch.definition.branch_type,
                "license_number": branch.definition.license_number,
                "license_expires_at": today + timedelta(days=730 + branch.index * 45),
                "working_hours": _working_hours(),
                "receipt_header": {
                    "line1": SHOWCASE_TENANT_LEGAL_NAME,
                    "line2": branch.definition.name,
                    "phone": "+992 00 000 00 01",
                    "demo_notice": "Демонстрационный чек",
                },
                "is_active": True,
                "created_at": _utc_at(today - timedelta(days=500), 9),
                "updated_at": utc_now(),
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
    await _bulk_insert(session, Branch.__table__, branch_rows)

    registers: list[RegisterRecord] = []
    for branch in branches:
        count = 2 if profile.branches > 1 else 2
        for register_index in range(count):
            registers.append(
                RegisterRecord(
                    id=_uuid(f"register:{branch.index}:{register_index}"),
                    branch_id=branch.id,
                    branch_index=branch.index,
                    code=f"{branch.index + 1:02d}-{register_index + 1:02d}",
                    is_active=not (
                        profile.name == "realistic" and branch.index == 2 and register_index == 1
                    ),
                )
            )
    if profile.name in {"realistic", "stress"}:
        registers.append(
            RegisterRecord(
                id=_uuid("register:retired"),
                branch_id=branches[0].id,
                branch_index=0,
                code="01-99",
                is_active=False,
            )
        )

    register_rows: list[Row] = []
    for register in registers:
        register_rows.append(
            {
                "id": register.id,
                "tenant_id": tenant_id,
                "branch_id": register.branch_id,
                "name": (
                    f"Касса {register.code}"
                    if register.code != "01-99"
                    else "Резервная касса (выведена)"
                ),
                "printer_type": "thermal_80",
                "printer_config": {
                    "paper_width_mm": 80,
                    "copies": 1,
                    "auto_print": register.is_active,
                },
                "is_active": register.is_active,
                "created_at": _utc_at(today - timedelta(days=480), 10),
                "updated_at": utc_now(),
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
    await _bulk_insert(session, Register.__table__, register_rows)
    return branches, registers


async def seed_roles_and_employees(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    branches: Sequence[BranchRecord],
    today: date,
) -> tuple[list[EmployeeRecord], dict[int, list[UUID]]]:
    role_ids = {definition.key: _uuid(f"role:{definition.key}") for definition in _ROLE_DEFINITIONS}
    role_rows: list[Row] = []
    permission_rows: list[Row] = []
    created_at = _utc_at(today - timedelta(days=455), 11)
    for definition in _ROLE_DEFINITIONS:
        role_id = role_ids[definition.key]
        role_rows.append(
            {
                "id": role_id,
                "tenant_id": tenant_id,
                "name": definition.name,
                "description": definition.description,
                "level": definition.level,
                "is_system": False,
                "is_active": True,
                "is_protected": False,
                "protected_kind": None,
                "version": 1,
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
        permission_rows.extend(
            {
                "role_id": role_id,
                "permission_code": permission,
                "created_at": created_at,
            }
            for permission in definition.permissions
        )
    await _bulk_insert(session, Role.__table__, role_rows)
    await _bulk_insert(session, RolePermission.__table__, permission_rows)

    applicable = [
        employee
        for employee in _EMPLOYEES
        if employee.branch_index is None or employee.branch_index < len(branches)
    ]
    password_hashes = {
        employee.key: hash_password(EMPLOYEE_PASSWORD) for employee in applicable if employee.active
    }
    user_rows: list[Row] = []
    membership_rows: list[Row] = []
    assignment_rows: list[Row] = []
    employees: list[EmployeeRecord] = []
    cashiers_by_branch: dict[int, list[UUID]] = defaultdict(list)

    for index, employee in enumerate(applicable):
        user_id = _uuid(f"user:{employee.key}")
        membership_id = _uuid(f"membership:{employee.key}")
        branch_id = (
            branches[employee.branch_index].id if employee.branch_index is not None else None
        )
        invited_at = created_at + timedelta(days=index)
        status = "active" if employee.active else "blocked"
        membership_status = "active" if employee.active else "suspended"
        user_rows.append(
            {
                "id": user_id,
                "email": f"{employee.key}@showcase.aurum.invalid",
                "full_name": employee.full_name,
                "phone": f"+992 00 000 {index + 10:02d} {index + 20:02d}",
                "password_hash": password_hashes.get(employee.key),
                "is_developer": False,
                "is_administrator": False,
                "home_tenant_id": tenant_id,
                "status": status,
                "invited_at": invited_at,
                "activated_at": invited_at + timedelta(hours=3) if employee.active else None,
                "blocked_at": utc_now() - timedelta(days=45) if not employee.active else None,
                "created_at": invited_at,
                "updated_at": invited_at,
            }
        )
        membership_rows.append(
            {
                "id": membership_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "full_name": employee.full_name,
                "phone": f"+992 00 000 {index + 10:02d} {index + 20:02d}",
                "status": membership_status,
                "invited_at": invited_at,
                "activated_at": invited_at + timedelta(hours=3) if employee.active else None,
                "suspended_at": utc_now() - timedelta(days=45) if not employee.active else None,
                "created_at": invited_at,
                "updated_at": invited_at,
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
        assignment_rows.append(
            {
                "id": _uuid(f"assignment:{employee.key}"),
                "user_id": user_id,
                "tenant_id": tenant_id,
                "membership_id": membership_id,
                "branch_id": branch_id,
                "role_id": role_ids[employee.role_key],
                "password_required": False,
                "is_active": employee.active,
                "created_at": invited_at,
                "updated_at": invited_at,
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
        employees.append(
            EmployeeRecord(
                user_id=user_id,
                membership_id=membership_id,
                role_key=employee.role_key,
                branch_id=branch_id,
                active=employee.active,
            )
        )
        if employee.active and employee.role_key in {"cashier", "senior_pharmacist"}:
            if employee.branch_index is not None:
                cashiers_by_branch[employee.branch_index].append(user_id)

    await _bulk_insert(session, AppUser.__table__, user_rows)
    await _bulk_insert(session, TenantMembership.__table__, membership_rows)
    await _bulk_insert(session, UserAssignment.__table__, assignment_rows)
    return employees, cashiers_by_branch


async def seed_suppliers(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    today: date,
) -> list[UUID]:
    rows: list[Row] = []
    ids: list[UUID] = []
    for index, (name, notes) in enumerate(_SUPPLIERS):
        supplier_id = _uuid(f"supplier:{index}")
        ids.append(supplier_id)
        rows.append(
            {
                "id": supplier_id,
                "tenant_id": tenant_id,
                "name": name,
                "legal_name": f"ООО «{name}»",
                "inn_or_tin": f"DEMO-SUP-{index + 1:06d}",
                "contact_person": f"Контакт поставщика {index + 1}",
                "phone": f"+992 00 100 {index + 10:02d} {index + 30:02d}",
                "email": f"supplier-{index + 1}@showcase.aurum.invalid",
                "address": "Республика Таджикистан, демонстрационный адрес",
                "notes": notes,
                "is_active": index != len(_SUPPLIERS) - 1,
                "created_at": _utc_at(today - timedelta(days=470 - index), 10),
                "updated_at": utc_now(),
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
    await _bulk_insert(session, Supplier.__table__, rows)
    return ids


async def seed_catalog(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    profile: ShowcaseProfile,
    today: date,
) -> list[CatalogRecord]:
    seeds = showcase_catalog_rows()
    if profile.catalog_limit is not None:
        seeds = seeds[: profile.catalog_limit]

    master_rows: list[Row] = []
    catalog_rows: list[Row] = []
    barcode_rows: list[Row] = []
    records: list[CatalogRecord] = []
    created_at = _utc_at(today - timedelta(days=460), 9)
    for index, seed in enumerate(seeds, start=1):
        master_id = _uuid(f"master:{seed.stable_key}") if seed.master_linked else None
        catalog_id = _uuid(f"catalog:{seed.stable_key}")
        if master_id is not None:
            master_rows.append(
                {
                    "id": master_id,
                    "brand_name": seed.brand_name,
                    "inn": seed.inn,
                    "manufacturer": seed.manufacturer,
                    "form": seed.form,
                    "dosage": seed.dosage,
                    "pack_size": seed.pack_size,
                    "atx_code": seed.atx_code,
                    "dispensing_type": seed.dispensing_type,
                    "storage_type": seed.storage_type,
                    "is_active": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
        catalog_rows.append(
            {
                "id": catalog_id,
                "tenant_id": tenant_id,
                "master_id": master_id,
                "brand_name": seed.brand_name,
                "inn": seed.inn,
                "manufacturer": seed.manufacturer,
                "form": seed.form,
                "dosage": seed.dosage,
                "pack_size": seed.pack_size,
                "atx_code": seed.atx_code,
                "dispensing_type": seed.dispensing_type,
                "storage_type": seed.storage_type,
                "category": seed.category,
                "base_price": seed.price,
                "currency": "TJS",
                "is_active": True,
                "created_at": created_at,
                "updated_at": created_at,
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )
        barcode_rows.append(
            {
                "id": _uuid(f"barcode:{seed.stable_key}"),
                "tenant_id": tenant_id,
                "catalog_id": catalog_id,
                "code": _ean13(index),
                "code_type": "ean13",
                "created_at": created_at,
            }
        )
        records.append(
            CatalogRecord(
                id=catalog_id,
                seed=seed,
                demand_weight=_catalog_weight(seed, index - 1),
            )
        )

    await _bulk_insert(session, MasterCatalog.__table__, master_rows)
    await _bulk_insert(session, TenantCatalog.__table__, catalog_rows)
    await _bulk_insert(session, Barcode.__table__, barcode_rows)
    checklist = await session.get(OnboardingChecklist, tenant_id)
    if checklist is not None:
        checklist.catalog_items_count = len(records)
    return records


def _expiry_for_batch(
    rng: random.Random,
    *,
    received_on: date,
    today: date,
    catalog: CatalogRecord,
    baseline: bool,
) -> date:
    if baseline and rng.random() < 0.045:
        return today + timedelta(days=rng.randint(-75, 170))
    if catalog.seed.form in {"прибор", "медицинское изделие"}:
        return received_on + timedelta(days=rng.randint(900, 1_800))
    if catalog.seed.storage_type == "cold":
        return received_on + timedelta(days=rng.randint(270, 720))
    return received_on + timedelta(days=rng.randint(420, 1_250))


def _batch_quantity(
    rng: random.Random,
    *,
    catalog: CatalogRecord,
    baseline: bool,
) -> Decimal:
    if baseline:
        low, high = (55, 190) if catalog.seed.master_linked else (18, 70)
    else:
        low, high = (24, 120) if catalog.seed.master_linked else (10, 45)
    return _qty(Decimal(rng.randint(low, high)))


async def seed_incoming_and_batches(  # noqa: PLR0915 - one ordered ledger transaction
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    profile: ShowcaseProfile,
    branches: Sequence[BranchRecord],
    suppliers: Sequence[UUID],
    catalog: Sequence[CatalogRecord],
    today: date,
    rng: random.Random,
) -> tuple[list[BatchStock], int]:
    history_start = today - timedelta(days=profile.history_days)
    baseline_date = history_start - timedelta(days=35)
    document_plans: list[tuple[UUID, BranchRecord, UUID, date, str, list[CatalogRecord]]] = []

    document_index = 0
    baseline_chunk_size = 60
    for branch in branches:
        for offset in range(0, len(catalog), baseline_chunk_size):
            document_id = _uuid(f"incoming:baseline:{branch.index}:{offset}")
            document_plans.append(
                (
                    document_id,
                    branch,
                    suppliers[(branch.index + offset // baseline_chunk_size) % len(suppliers)],
                    baseline_date + timedelta(days=offset // baseline_chunk_size),
                    "accepted",
                    list(catalog[offset : offset + baseline_chunk_size]),
                )
            )
            document_index += 1

    remaining_documents = max(0, profile.incoming_documents - len(document_plans))
    weighted_catalog = list(catalog)
    weights = [record.demand_weight for record in weighted_catalog]
    date_span = profile.history_days + 25
    for extra_index in range(remaining_documents):
        branch = branches[extra_index % len(branches)]
        document_date = (
            history_start - timedelta(days=20) + timedelta(days=rng.randint(0, date_span))
        )
        roll = rng.random()
        status = "accepted" if roll < 0.91 else ("draft" if roll < 0.965 else "rejected")
        item_count = rng.randint(10, 24)
        selected: dict[UUID, CatalogRecord] = {}
        for record in rng.choices(weighted_catalog, weights=weights, k=item_count * 2):
            selected.setdefault(record.id, record)
            if len(selected) >= item_count:
                break
        document_id = _uuid(f"incoming:regular:{extra_index}")
        document_plans.append(
            (
                document_id,
                branch,
                suppliers[rng.randrange(len(suppliers))],
                document_date,
                status,
                list(selected.values()),
            )
        )
        document_index += 1

    document_plans.sort(key=lambda plan: (plan[3], str(plan[0])))
    document_rows: list[Row] = []
    item_rows: list[Row] = []
    batch_rows: list[Row] = []
    movement_rows: list[Row] = []
    stocks: list[BatchStock] = []

    for plan_index, (
        document_id,
        branch,
        supplier_id,
        document_date,
        status,
        selected_catalog,
    ) in enumerate(document_plans, start=1):
        baseline = document_date <= baseline_date + timedelta(days=12)
        accepted_at = _utc_at(document_date, 11, rng.randint(0, 50))
        document_total = Decimal("0")
        per_document_items: list[tuple[CatalogRecord, Decimal, Decimal, Decimal, date]] = []
        for record in selected_catalog:
            qty = _batch_quantity(rng, catalog=record, baseline=baseline)
            purchase_factor = Decimal(str(rng.uniform(0.61, 0.79)))
            purchase_price = _money(record.seed.price * purchase_factor)
            sale_variation = Decimal(str(rng.uniform(0.97, 1.06)))
            sale_price = _money(max(Decimal("0.10"), record.seed.price * sale_variation))
            expires_at = _expiry_for_batch(
                rng,
                received_on=document_date,
                today=today,
                catalog=record,
                baseline=baseline,
            )
            document_total += _money(qty * purchase_price)
            per_document_items.append((record, qty, purchase_price, sale_price, expires_at))

        document_rows.append(
            {
                "id": document_id,
                "tenant_id": tenant_id,
                "branch_id": branch.id,
                "supplier_id": supplier_id,
                "document_number": f"ПН-{document_date:%Y%m}-{plan_index:05d}",
                "document_date": document_date,
                "status": status,
                "total_amount": _money(document_total),
                "currency": "TJS",
                "notes": (
                    "Плановое пополнение ассортимента"
                    if status == "accepted"
                    else (
                        "Черновик ожидает сверки"
                        if status == "draft"
                        else "Отклонено при входном контроле"
                    )
                ),
                "document_file_path": None,
                "created_at": accepted_at - timedelta(hours=2),
                "updated_at": accepted_at,
                "accepted_at": accepted_at if status == "accepted" else None,
                "created_by": owner_id,
                "updated_by": owner_id,
                "accepted_by": owner_id if status == "accepted" else None,
            }
        )

        for item_index, (
            record,
            qty,
            purchase_price,
            sale_price,
            expires_at,
        ) in enumerate(per_document_items, start=1):
            item_id = _uuid(f"incoming-item:{document_id}:{item_index}")
            batch_id = _uuid(f"batch:{document_id}:{item_index}") if status == "accepted" else None
            batch_number = f"{document_date:%y%m}{plan_index:04d}{item_index:03d}"
            manufactured_at = document_date - timedelta(days=rng.randint(25, 240))
            item_rows.append(
                {
                    "id": item_id,
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "catalog_id": record.id,
                    "batch_number": batch_number,
                    "manufactured_at": manufactured_at,
                    "expires_at": expires_at,
                    "qty": qty,
                    "purchase_price": purchase_price,
                    "sale_price": sale_price,
                    "currency": "TJS",
                    "created_batch_id": batch_id,
                    "created_at": accepted_at - timedelta(hours=1),
                    "updated_at": accepted_at,
                }
            )
            if batch_id is None:
                continue
            batch_rows.append(
                {
                    "id": batch_id,
                    "tenant_id": tenant_id,
                    "branch_id": branch.id,
                    "catalog_id": record.id,
                    "batch_number": batch_number,
                    "manufactured_at": manufactured_at,
                    "expires_at": expires_at,
                    "purchase_price": purchase_price,
                    "sale_price": sale_price,
                    "currency": "TJS",
                    "qty_initial": qty,
                    "qty_remaining": Decimal("0.000"),
                    "is_blocked": False,
                    "block_reason": None,
                    "blocked_at": None,
                    "created_at": accepted_at,
                    "updated_at": accepted_at,
                    "created_by": owner_id,
                    "updated_by": owner_id,
                }
            )
            movement_rows.append(
                {
                    "id": _uuid(f"movement:incoming:{item_id}"),
                    "tenant_id": tenant_id,
                    "batch_id": batch_id,
                    "movement_type": "incoming",
                    "qty_delta": qty,
                    "source_table": "incoming_item",
                    "source_id": item_id,
                    "operation_key": f"showcase:incoming:{item_id}",
                    "notes": None,
                    "created_at": accepted_at,
                    "created_by": owner_id,
                }
            )
            stocks.append(
                BatchStock(
                    id=batch_id,
                    branch_id=branch.id,
                    catalog=record,
                    supplier_id=supplier_id,
                    incoming_document_id=document_id,
                    received_at=accepted_at,
                    expires_at=expires_at,
                    purchase_price=purchase_price,
                    sale_price=sale_price,
                    qty_initial=qty,
                    qty_remaining=qty,
                )
            )

    await _bulk_insert(session, IncomingDocument.__table__, document_rows)
    await _bulk_insert(session, Batch.__table__, batch_rows)
    await _bulk_insert(session, IncomingItem.__table__, item_rows)
    await _bulk_insert(session, BatchMovement.__table__, movement_rows)
    return stocks, len(document_rows)


def _daily_sales_count(
    rng: random.Random,
    *,
    base: int,
    local_date: date,
    branch_index: int,
) -> int:
    weekday_factor = 0.84 if local_date.weekday() == 6 else 1.04
    seasonal_factor = 1.18 if local_date.month in {1, 2, 11, 12} else 0.94
    branch_factor = max(0.72, 1.08 - branch_index * 0.11)
    jitter = rng.uniform(0.76, 1.25)
    return max(2, round(base * weekday_factor * seasonal_factor * branch_factor * jitter))


def _choose_sale_time(rng: random.Random, local_date: date) -> datetime:
    hour_value = rng.triangular(8.0, 21.7, 14.3)
    hour = min(21, max(8, int(hour_value)))
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    return _utc_at(local_date, hour, minute, second)


def _find_stock(
    rng: random.Random,
    *,
    branch_id: UUID,
    occurred_at: datetime,
    requested_qty: Decimal,
    weighted_catalog: Sequence[CatalogRecord],
    weights: Sequence[float],
    stocks_by_key: dict[tuple[UUID, UUID], list[BatchStock]],
    excluded_catalog_ids: set[UUID],
) -> BatchStock | None:
    occurred_on = occurred_at.astimezone(LOCAL_TIMEZONE).date()
    for candidate in rng.choices(weighted_catalog, weights=weights, k=25):
        if candidate.id in excluded_catalog_ids:
            continue
        candidates = stocks_by_key.get((branch_id, candidate.id), [])
        valid = [
            stock
            for stock in candidates
            if stock.received_at <= occurred_at
            and stock.expires_at >= occurred_on
            and stock.qty_remaining >= requested_qty
        ]
        if valid:
            return min(valid, key=lambda stock: (stock.expires_at, stock.received_at))
    return None


def _payment_plans(
    rng: random.Random,
    *,
    sale_id: UUID,
    total: Decimal,
    return_payment_method: str | None = None,
) -> list[PaymentPlan]:
    if return_payment_method is not None:
        return [
            PaymentPlan(
                id=_uuid(f"payment:{sale_id}:0"),
                method=return_payment_method,
                amount=total,
            )
        ]
    roll = rng.random()
    methods: tuple[str, ...]
    if roll < 0.63:
        methods = ("cash",)
    elif roll < 0.93:
        methods = ("card",)
    elif roll < 0.97:
        methods = ("bank_transfer",)
    else:
        methods = ("cash", "card")
    if len(methods) == 1:
        return [
            PaymentPlan(
                id=_uuid(f"payment:{sale_id}:0"),
                method=methods[0],
                amount=total,
            )
        ]
    cash = _money(total * Decimal(str(rng.uniform(0.25, 0.7))))
    return [
        PaymentPlan(id=_uuid(f"payment:{sale_id}:0"), method="cash", amount=cash),
        PaymentPlan(
            id=_uuid(f"payment:{sale_id}:1"),
            method="card",
            amount=_money(total - cash),
        ),
    ]


def generate_sales(
    *,
    tenant_id: UUID,
    owner_id: UUID,
    profile: ShowcaseProfile,
    branches: Sequence[BranchRecord],
    registers: Sequence[RegisterRecord],
    cashiers_by_branch: dict[int, list[UUID]],
    catalog: Sequence[CatalogRecord],
    stocks: Sequence[BatchStock],
    today: date,
    rng: random.Random,
) -> list[SalePlan]:
    active_registers: dict[int, list[RegisterRecord]] = defaultdict(list)
    for register in registers:
        if register.is_active:
            active_registers[register.branch_index].append(register)

    stocks_by_key: dict[tuple[UUID, UUID], list[BatchStock]] = defaultdict(list)
    for batch_stock in stocks:
        stocks_by_key[(batch_stock.branch_id, batch_stock.catalog.id)].append(batch_stock)
    for batch_list in stocks_by_key.values():
        batch_list.sort(key=lambda stock: (stock.expires_at, stock.received_at))

    weighted_catalog = list(catalog)
    weights = [record.demand_weight for record in weighted_catalog]
    history_start = today - timedelta(days=profile.history_days - 1)
    sales: list[SalePlan] = []
    sale_index = 0

    for day_offset in range(profile.history_days):
        local_date = history_start + timedelta(days=day_offset)
        for branch in branches:
            count = _daily_sales_count(
                rng,
                base=profile.daily_sales_per_branch,
                local_date=local_date,
                branch_index=branch.index,
            )
            branch_registers = active_registers[branch.index]
            branch_cashiers = cashiers_by_branch.get(branch.index) or [owner_id]
            for _ in range(count):
                occurred_at = _choose_sale_time(rng, local_date)
                register = rng.choice(branch_registers)
                cashier_id = rng.choice(branch_cashiers)
                sale_id = _uuid(f"sale:{sale_index}")
                item_target = rng.choices((1, 2, 3, 4), weights=(42, 35, 18, 5), k=1)[0]
                items: list[SaleItemPlan] = []
                selected_catalog_ids: set[UUID] = set()

                for position in range(item_target):
                    requested_qty = Decimal(rng.choices((1, 2, 3), weights=(83, 14, 3), k=1)[0])
                    selected_stock = _find_stock(
                        rng,
                        branch_id=branch.id,
                        occurred_at=occurred_at,
                        requested_qty=requested_qty,
                        weighted_catalog=weighted_catalog,
                        weights=weights,
                        stocks_by_key=stocks_by_key,
                        excluded_catalog_ids=selected_catalog_ids,
                    )
                    if selected_stock is None:
                        continue
                    selected_catalog_ids.add(selected_stock.catalog.id)
                    selected_stock.qty_remaining = _qty(
                        selected_stock.qty_remaining - requested_qty
                    )
                    unit_price = selected_stock.sale_price
                    gross = _money(unit_price * requested_qty)
                    discount = (
                        _money(gross * Decimal(str(rng.uniform(0.02, 0.08))))
                        if rng.random() < 0.075
                        else Decimal("0.00")
                    )
                    total = _money(gross - discount)
                    item_id = _uuid(f"sale-item:{sale_id}:{position}")
                    items.append(
                        SaleItemPlan(
                            id=item_id,
                            catalog_id=selected_stock.catalog.id,
                            batch_id=selected_stock.id,
                            qty=_qty(requested_qty),
                            unit_price=unit_price,
                            discount_amount=discount,
                            total_price=total,
                        )
                    )

                if not items:
                    continue
                sale = SalePlan(
                    id=sale_id,
                    tenant_id=tenant_id,
                    branch_id=branch.id,
                    register_id=register.id,
                    cashier_user_id=cashier_id,
                    occurred_at=occurred_at,
                    sale_type="sale",
                    status="completed",
                    items=items,
                    payments=[],
                )
                sale.payments = _payment_plans(
                    rng,
                    sale_id=sale.id,
                    total=sale.total_amount,
                )
                sale.prescriptions = [
                    item.id
                    for item in items
                    if next(
                        record.seed.dispensing_type
                        for record in catalog
                        if record.id == item.catalog_id
                    )
                    in {"prescription", "special"}
                ]
                sales.append(sale)
                sale_index += 1

    return sales


def generate_returns(
    *,
    sales: list[SalePlan],
    stocks_by_id: dict[UUID, BatchStock],
    today: date,
    rng: random.Random,
) -> list[SalePlan]:
    eligible = [
        sale
        for sale in sales
        if sale.occurred_at.astimezone(LOCAL_TIMEZONE).date() <= today - timedelta(days=2)
    ]
    return_count = max(1, round(len(eligible) * 0.012))
    selected_sales = rng.sample(eligible, k=min(return_count, len(eligible)))
    returns: list[SalePlan] = []

    for index, original in enumerate(selected_sales):
        original_item = rng.choice(original.items)
        original_date = original.occurred_at.astimezone(LOCAL_TIMEZONE).date()
        latest_delay = max(1, min(30, (today - original_date).days))
        return_date = original_date + timedelta(days=rng.randint(1, latest_delay))
        return_at = _utc_at(return_date, rng.randint(9, 20), rng.randint(0, 59))
        qty = original_item.qty if rng.random() < 0.22 else min(Decimal("1.000"), original_item.qty)
        ratio = qty / original_item.qty
        discount = _money(original_item.discount_amount * ratio)
        total = _money(original_item.total_price * ratio)
        return_id = _uuid(f"return:{index}:{original.id}")
        return_item = SaleItemPlan(
            id=_uuid(f"return-item:{return_id}"),
            catalog_id=original_item.catalog_id,
            batch_id=original_item.batch_id,
            qty=_qty(qty),
            unit_price=original_item.unit_price,
            discount_amount=discount,
            total_price=total,
            parent_sale_item_id=original_item.id,
        )
        payment_method = original.payments[0].method
        return_sale = SalePlan(
            id=return_id,
            tenant_id=original.tenant_id,
            branch_id=original.branch_id,
            register_id=original.register_id,
            cashier_user_id=original.cashier_user_id,
            occurred_at=return_at,
            sale_type="return",
            status="completed",
            parent_sale_id=original.id,
            items=[return_item],
            payments=_payment_plans(
                rng,
                sale_id=return_id,
                total=total,
                return_payment_method=payment_method,
            ),
        )
        stocks_by_id[original_item.batch_id].qty_remaining = _qty(
            stocks_by_id[original_item.batch_id].qty_remaining + qty
        )
        if len(original.items) == 1 and qty == original_item.qty:
            original.status = "voided"
            original.voided_at = return_at
            original.voided_by_sale_id = return_id
        returns.append(return_sale)
    return returns


def assign_receipts(
    sales: Sequence[SalePlan],
    registers_by_id: dict[UUID, RegisterRecord],
) -> dict[UUID, int]:
    counters: dict[UUID, int] = defaultdict(int)
    for sale in sorted(sales, key=lambda item: (item.occurred_at, str(item.id))):
        if sale.status not in {"completed", "voided"}:
            continue
        counters[sale.register_id] += 1
        sequence = counters[sale.register_id]
        register = registers_by_id[sale.register_id]
        sale.receipt_seq = sequence
        sale.receipt_number = f"{register.code}-{sequence:06d}"
    return dict(counters)


def build_shifts(
    sales: Sequence[SalePlan],
) -> dict[tuple[UUID, date], ShiftRecord]:
    shifts: dict[tuple[UUID, date], ShiftRecord] = {}
    for sale in sorted(sales, key=lambda item: item.occurred_at):
        local_date = sale.occurred_at.astimezone(LOCAL_TIMEZONE).date()
        key = (sale.register_id, local_date)
        if key not in shifts:
            shifts[key] = ShiftRecord(
                id=_uuid(f"shift:{sale.register_id}:{local_date.isoformat()}"),
                register_id=sale.register_id,
                branch_id=sale.branch_id,
                local_date=local_date,
                opened_by_user_id=sale.cashier_user_id,
            )
    return shifts


async def seed_sales_and_shifts(  # noqa: PLR0912,PLR0915 - one immutable plan
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    sales: list[SalePlan],
    returns: list[SalePlan],
    registers: Sequence[RegisterRecord],
    today: date,
    rng: random.Random,
) -> tuple[int, int]:
    all_sales = [*sales, *returns]
    registers_by_id = {register.id: register for register in registers}
    counters = assign_receipts(all_sales, registers_by_id)
    shifts = build_shifts(all_sales)

    totals_by_shift: dict[UUID, dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "cash": Decimal("0"),
            "card": Decimal("0"),
            "bank_transfer": Decimal("0"),
            "sales_count": 0,
            "returns_count": 0,
        }
    )
    for sale in all_sales:
        local_date = sale.occurred_at.astimezone(LOCAL_TIMEZONE).date()
        shift = shifts[(sale.register_id, local_date)]
        if sale.status == "completed":
            count_key = "sales_count" if sale.sale_type == "sale" else "returns_count"
            current_count = totals_by_shift[shift.id][count_key]
            if not isinstance(current_count, int):
                raise RuntimeError("Invalid shift count accumulator")
            totals_by_shift[shift.id][count_key] = current_count + 1
            for payment in sale.payments:
                current_amount = totals_by_shift[shift.id][payment.method]
                if not isinstance(current_amount, Decimal):
                    raise RuntimeError("Invalid shift payment accumulator")
                totals_by_shift[shift.id][payment.method] = current_amount + payment.amount

    shift_rows: list[Row] = []
    for shift in shifts.values():
        totals = totals_by_shift[shift.id]
        opening_cash = Decimal("500.00")
        cash_total = totals["cash"]
        if not isinstance(cash_total, Decimal):
            raise RuntimeError("Invalid cash total")
        expected = _money(opening_cash + cash_total)
        is_open = shift.local_date == today
        difference = Decimal(str(rng.choice(("0.00", "0.00", "0.00", "0.10", "-0.10"))))
        shift_rows.append(
            {
                "id": shift.id,
                "tenant_id": tenant_id,
                "branch_id": shift.branch_id,
                "register_id": shift.register_id,
                "opened_by_user_id": shift.opened_by_user_id,
                "closed_by_user_id": None if is_open else shift.opened_by_user_id,
                "opened_at": _utc_at(shift.local_date, 7, 55),
                "closed_at": None if is_open else _utc_at(shift.local_date, 22, 5),
                "status": "open" if is_open else "closed",
                "opening_cash": opening_cash,
                "closing_cash_actual": None if is_open else _money(expected + difference),
                "closing_cash_expected": None if is_open else expected,
                "closing_difference": None if is_open else difference,
                "totals": (
                    None
                    if is_open
                    else {
                        "cash": str(_money(cash_total)),
                        "card": str(_money(_decimal_total(totals["card"]))),
                        "bank_transfer": str(_money(_decimal_total(totals["bank_transfer"]))),
                        "sales_count": _integer_total(totals["sales_count"]),
                        "returns_count": _integer_total(totals["returns_count"]),
                    }
                ),
                "currency": "TJS",
                "notes": None if is_open else "Смена закрыта без существенных расхождений",
            }
        )
    await _bulk_insert(session, Shift.__table__, shift_rows)

    sale_rows: list[Row] = []
    item_rows: list[Row] = []
    payment_rows: list[Row] = []
    movement_rows: list[Row] = []
    prescription_rows: list[Row] = []
    for sale in sorted(all_sales, key=lambda item: (item.occurred_at, str(item.id))):
        local_date = sale.occurred_at.astimezone(LOCAL_TIMEZONE).date()
        shift_id = shifts[(sale.register_id, local_date)].id
        operation_id = _uuid(f"operation:sale:{sale.id}")
        sale_rows.append(
            {
                "id": sale.id,
                "tenant_id": tenant_id,
                "branch_id": sale.branch_id,
                "register_id": sale.register_id,
                "shift_id": shift_id,
                "sale_type": sale.sale_type,
                "parent_sale_id": sale.parent_sale_id,
                # A full refund forms a FK cycle: the return points to its
                # original and the original points back to the return. Insert
                # the original as completed, then apply the valid void
                # transition after both rows exist.
                "status": "completed" if sale.status == "voided" else sale.status,
                "receipt_number": sale.receipt_number,
                "receipt_seq": sale.receipt_seq,
                "operation_id": operation_id,
                "operation_hash": _operation_hash("sale", operation_id),
                "is_test": False,
                "total_amount": sale.total_amount,
                "currency": "TJS",
                "voided_at": None,
                "voided_by_sale_id": None,
                "cashier_user_id": sale.cashier_user_id,
                "created_at": sale.occurred_at - timedelta(minutes=rng.randint(1, 7)),
                "completed_at": sale.occurred_at,
                "fiscal_data": {
                    "mode": "showcase",
                    "fiscalized": False,
                    "notice": "Демонстрационные данные",
                },
                "marking_codes": None,
            }
        )
        for position, item in enumerate(sale.items, start=1):
            item_rows.append(
                {
                    "id": item.id,
                    "tenant_id": tenant_id,
                    "sale_id": sale.id,
                    "parent_sale_item_id": item.parent_sale_item_id,
                    "catalog_id": item.catalog_id,
                    "batch_id": item.batch_id,
                    "qty": item.qty,
                    "unit_price": item.unit_price,
                    "total_price": item.total_price,
                    "currency": "TJS",
                    "discount_amount": item.discount_amount,
                    "position": position,
                    "created_at": sale.occurred_at,
                }
            )
            movement_type = "sale_return" if sale.sale_type == "return" else "sale"
            delta = item.qty if sale.sale_type == "return" else -item.qty
            movement_rows.append(
                {
                    "id": _uuid(f"movement:{movement_type}:{item.id}"),
                    "tenant_id": tenant_id,
                    "batch_id": item.batch_id,
                    "movement_type": movement_type,
                    "qty_delta": delta,
                    "source_table": "sale_item",
                    "source_id": item.id,
                    "operation_key": f"showcase:{movement_type}:{item.id}",
                    "notes": None,
                    "created_at": sale.occurred_at,
                    "created_by": sale.cashier_user_id,
                }
            )
        for payment in sale.payments:
            payment_operation_id = _uuid(f"operation:payment:{payment.id}")
            payment_rows.append(
                {
                    "id": payment.id,
                    "tenant_id": tenant_id,
                    "sale_id": sale.id,
                    "payment_method": payment.method,
                    "amount": payment.amount,
                    "operation_id": payment_operation_id,
                    "operation_hash": _operation_hash("payment", payment_operation_id),
                    "currency": "TJS",
                    "metadata": {
                        "mode": "showcase",
                        "terminal": (
                            f"DEMO-{registers_by_id[sale.register_id].code}"
                            if payment.method == "card"
                            else None
                        ),
                    },
                    "created_at": sale.occurred_at,
                }
            )
        for prescription_index, sale_item_id in enumerate(sale.prescriptions):
            prescription_rows.append(
                {
                    "id": _uuid(f"prescription:{sale.id}:{prescription_index}"),
                    "tenant_id": tenant_id,
                    "sale_id": sale.id,
                    "sale_item_id": sale_item_id,
                    "prescription_number": (
                        f"DEMO-RX-{sale.occurred_at:%Y%m%d}-{prescription_index + 1:02d}"
                    ),
                    "doctor_name": "Врач (демонстрационные данные)",
                    "doctor_license": "DEMO-LIC-0000",
                    "patient_name": "Пациент (демонстрационные данные)",
                    "notes": "Тестовая запись, не является медицинским документом",
                    "created_at": sale.occurred_at,
                    "created_by": sale.cashier_user_id,
                }
            )

    await _bulk_insert(session, Sale.__table__, sale_rows)
    await _bulk_insert(session, SaleItem.__table__, item_rows)
    await _bulk_insert(session, SalePayment.__table__, payment_rows)
    await _bulk_insert(session, PrescriptionLog.__table__, prescription_rows)
    await _bulk_insert(session, BatchMovement.__table__, movement_rows)

    for original in (sale for sale in sales if sale.status == "voided"):
        await session.execute(
            text("""
                UPDATE public.sale
                SET
                  status = 'voided',
                  voided_at = :voided_at,
                  voided_by_sale_id = :voided_by_sale_id
                WHERE id = :sale_id
                  AND tenant_id = :tenant_id
                  AND status = 'completed'
                """),
            {
                "voided_at": original.voided_at,
                "voided_by_sale_id": original.voided_by_sale_id,
                "sale_id": original.id,
                "tenant_id": tenant_id,
            },
        )

    for register_id, last_receipt_seq in counters.items():
        register = registers_by_id[register_id]
        await session.execute(
            text("""
                INSERT INTO public.register_receipt_counter (
                  tenant_id,
                  branch_id,
                  register_id,
                  writer_epoch,
                  last_receipt_seq,
                  created_by,
                  updated_by
                )
                SELECT
                  :tenant_id,
                  :branch_id,
                  :register_id,
                  sync_stream.writer_epoch,
                  :last_receipt_seq,
                  :owner_id,
                  :owner_id
                FROM public.sync_stream AS sync_stream
                WHERE sync_stream.tenant_id = :tenant_id
                  AND sync_stream.branch_id = :branch_id
                ON CONFLICT (tenant_id, register_id) DO UPDATE
                SET
                  writer_epoch = EXCLUDED.writer_epoch,
                  last_receipt_seq = EXCLUDED.last_receipt_seq,
                  updated_by = EXCLUDED.updated_by
                """),
            {
                "tenant_id": tenant_id,
                "branch_id": register.branch_id,
                "register_id": register_id,
                "last_receipt_seq": last_receipt_seq,
                "owner_id": owner_id,
            },
        )
    return len(sales), len(returns)


def _decimal_total(value: Decimal | int) -> Decimal:
    if not isinstance(value, Decimal):
        raise RuntimeError("Expected a decimal total")
    return value


def _integer_total(value: Decimal | int) -> int:
    if not isinstance(value, int):
        raise RuntimeError("Expected an integer total")
    return value


async def seed_inventory_adjustments(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    stocks: Sequence[BatchStock],
    today: date,
    rng: random.Random,
    profile: ShowcaseProfile,
) -> tuple[int, int]:
    available = [stock for stock in stocks if stock.qty_remaining >= Decimal("4.000")]
    write_off_target = min(len(available), max(8, profile.branches * 12))
    write_off_stocks = rng.sample(available, write_off_target)
    write_off_rows: list[Row] = []
    supplier_return_rows: list[Row] = []
    movement_rows: list[Row] = []

    reasons = ("expired", "damaged", "spoiled", "theft", "other")
    for index, stock in enumerate(write_off_stocks):
        max_qty = max(1, min(5, int(stock.qty_remaining) - 1))
        qty = _qty(Decimal(rng.randint(1, max_qty)))
        reason = "expired" if stock.expires_at < today else rng.choice(reasons[1:])
        event_id = _uuid(f"write-off:{index}:{stock.id}")
        event_at = _utc_at(today - timedelta(days=rng.randint(2, 160)), rng.randint(9, 18))
        write_off_rows.append(
            {
                "id": event_id,
                "tenant_id": tenant_id,
                "branch_id": stock.branch_id,
                "batch_id": stock.id,
                "qty": qty,
                "reason": reason,
                "comment": ("Плановое списание по результатам инвентарного контроля"),
                "amount": _money(qty * stock.purchase_price),
                "currency": "TJS",
                "created_at": event_at,
                "created_by": owner_id,
            }
        )
        movement_rows.append(
            {
                "id": _uuid(f"movement:write-off:{event_id}"),
                "tenant_id": tenant_id,
                "batch_id": stock.id,
                "movement_type": "write_off",
                "qty_delta": -qty,
                "source_table": "write_off",
                "source_id": event_id,
                "operation_key": f"showcase:write_off:{event_id}",
                "notes": reason,
                "created_at": event_at,
                "created_by": owner_id,
            }
        )
        stock.qty_remaining = _qty(stock.qty_remaining - qty)

    return_candidates = [stock for stock in available if stock.qty_remaining >= Decimal("6.000")]
    return_target = min(len(return_candidates), max(5, profile.branches * 6))
    for index, stock in enumerate(rng.sample(return_candidates, return_target)):
        qty = _qty(Decimal(rng.randint(1, min(4, int(stock.qty_remaining) - 1))))
        event_id = _uuid(f"supplier-return:{index}:{stock.id}")
        event_at = _utc_at(today - timedelta(days=rng.randint(3, 120)), rng.randint(10, 17))
        supplier_return_rows.append(
            {
                "id": event_id,
                "tenant_id": tenant_id,
                "supplier_id": stock.supplier_id,
                "source_document_id": stock.incoming_document_id,
                "batch_id": stock.id,
                "qty": qty,
                "amount": _money(qty * stock.purchase_price),
                "currency": "TJS",
                "reason": rng.choice(
                    (
                        "Повреждение упаковки при приёмке",
                        "Несоответствие согласованному ассортименту",
                        "Возврат по договорённости с поставщиком",
                    )
                ),
                "comment": "Демонстрационная операция возврата поставщику",
                "created_at": event_at,
                "created_by": owner_id,
            }
        )
        movement_rows.append(
            {
                "id": _uuid(f"movement:supplier-return:{event_id}"),
                "tenant_id": tenant_id,
                "batch_id": stock.id,
                "movement_type": "supplier_return",
                "qty_delta": -qty,
                "source_table": "supplier_return",
                "source_id": event_id,
                "operation_key": f"showcase:supplier_return:{event_id}",
                "notes": None,
                "created_at": event_at,
                "created_by": owner_id,
            }
        )
        stock.qty_remaining = _qty(stock.qty_remaining - qty)

    await _bulk_insert(session, WriteOff.__table__, write_off_rows)
    await _bulk_insert(session, SupplierReturn.__table__, supplier_return_rows)
    await _bulk_insert(session, BatchMovement.__table__, movement_rows)
    return len(write_off_rows), len(supplier_return_rows)


async def seed_billing(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    branch_count: int,
    today: date,
) -> None:
    plan = await session.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.code == "aurum_pharma")
    )
    if plan is None:
        raise RuntimeError("Aurum Pharma subscription plan is missing")
    subscription_id = _uuid("billing:main-subscription")
    month_start = today.replace(day=1)
    period_start = _utc_at(month_start, 0)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    amount = _money(plan.price_per_branch * branch_count)
    await _bulk_insert(
        session,
        TenantSubscription.__table__,
        [
            {
                "id": subscription_id,
                "tenant_id": tenant_id,
                "plan_id": plan.id,
                "status": "active",
                "billing_period": "monthly",
                "period_start": period_start,
                "period_end": _utc_at(next_month, 0),
                "branches_count": branch_count,
                "amount": amount,
                "currency": "TJS",
                "created_at": period_start - timedelta(days=330),
                "updated_at": period_start,
                "cancelled_at": None,
            }
        ],
    )

    invoice_rows: list[Row] = []
    payment_rows: list[Row] = []
    for months_ago in range(11, -1, -1):
        issued_month = _subtract_months(month_start, months_ago)
        issued_at = _utc_at(issued_month, 9)
        invoice_id = _uuid(f"billing:invoice:{issued_month:%Y-%m}")
        is_current = months_ago == 0
        invoice_rows.append(
            {
                "id": invoice_id,
                "tenant_id": tenant_id,
                "subscription_id": subscription_id,
                "invoice_number": f"AP-DEMO-{issued_month:%Y%m}-0001",
                "issued_at": issued_at,
                "due_at": issued_at + timedelta(days=10),
                "amount": amount,
                "currency": "TJS",
                "discount_amount": Decimal("0.00"),
                "discount_reason": None,
                "status": "overdue" if is_current else "paid",
                "paid_at": None if is_current else issued_at + timedelta(days=3),
                "notes": "Демонстрационный счёт Aurum Pharma",
                "pdf_path": None,
                "created_at": issued_at,
                "updated_at": issued_at,
            }
        )
        if not is_current:
            payment_rows.append(
                {
                    "id": _uuid(f"billing:payment:{issued_month:%Y-%m}"),
                    "tenant_id": tenant_id,
                    "invoice_id": invoice_id,
                    "amount": amount,
                    "currency": "TJS",
                    "method": "bank_transfer",
                    "reference": f"DEMO-PAY-{issued_month:%Y%m}",
                    "paid_at": issued_at + timedelta(days=3),
                    "recorded_by": owner_id,
                    "notes": "Оплата за подписку (демонстрационные данные)",
                    "created_at": issued_at + timedelta(days=3),
                }
            )
    await _bulk_insert(session, Invoice.__table__, invoice_rows)
    await _bulk_insert(session, Payment.__table__, payment_rows)


def _subtract_months(value: date, months: int) -> date:
    absolute = value.year * 12 + value.month - 1 - months
    return date(absolute // 12, absolute % 12 + 1, 1)


async def seed_notifications(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    employees: Sequence[EmployeeRecord],
    today: date,
) -> None:
    recipients = [owner_id] + [
        employee.user_id
        for employee in employees
        if employee.active and employee.role_key in {"manager", "warehouse"}
    ]
    event_types = (
        "inventory.expiry_warning",
        "inventory.low_stock",
        "billing.invoice_issued",
        "shift.cash_difference",
        "security.new_login",
    )
    subscription_rows: list[Row] = []
    for user_id in recipients:
        for event_type in event_types:
            subscription_rows.append(
                {
                    "user_id": user_id,
                    "event_type": event_type,
                    "channels": ["in_app"],
                    "is_enabled": True,
                    "updated_at": utc_now(),
                }
            )

    notification_templates = (
        (
            "inventory.expiry_warning",
            "Товары с близким сроком годности",
            "Проверьте партии в красной и оранжевой зонах срока годности.",
            "warning",
        ),
        (
            "inventory.low_stock",
            "Низкий остаток по популярным позициям",
            "Для части ассортимента рекомендуется сформировать заказ поставщику.",
            "warning",
        ),
        (
            "billing.invoice_issued",
            "Сформирован новый счёт",
            "Счёт за текущий период доступен в разделе биллинга.",
            "info",
        ),
        (
            "shift.cash_difference",
            "Небольшое расхождение в кассовой смене",
            "Расхождение отмечено для проверки ответственным сотрудником.",
            "warning",
        ),
        (
            "security.new_login",
            "Новый вход в аккаунт",
            "Зафиксирован вход с нового устройства в демонстрационной среде.",
            "info",
        ),
        (
            "inventory.expiry_warning",
            "Есть просроченные партии",
            "Продажа заблокирована. Выполните проверку и оформите списание.",
            "critical",
        ),
    )
    notification_rows: list[Row] = []
    delivery_rows: list[Row] = []
    for index, template in enumerate(notification_templates):
        event_type, title, body, severity = template
        notification_id = _uuid(f"notification:{index}")
        recipient = recipients[index % len(recipients)]
        created_at = _utc_at(today - timedelta(days=index * 2), 10 + index)
        notification_rows.append(
            {
                "id": notification_id,
                "tenant_id": tenant_id,
                "user_id": recipient,
                "event_type": event_type,
                "title": title,
                "body": body,
                "data": {"showcase": True, "section": event_type.split(".")[0]},
                "severity": severity,
                "read_at": created_at + timedelta(hours=2) if index >= 3 else None,
                "created_at": created_at,
            }
        )
        if index == 2:
            delivery_rows.append(
                {
                    "id": _uuid(f"notification-delivery:{index}"),
                    "notification_id": notification_id,
                    "channel": "email",
                    "recipient": "owner@showcase.aurum.invalid",
                    "status": "sent",
                    "error_message": None,
                    "attempts": 1,
                    "sent_at": created_at + timedelta(minutes=1),
                    "created_at": created_at,
                }
            )
    await _bulk_insert(session, NotificationSubscription.__table__, subscription_rows)
    await _bulk_insert(session, Notification.__table__, notification_rows)
    await _bulk_insert(session, NotificationDelivery.__table__, delivery_rows)


async def seed_extra_tenants(
    session: AsyncSession,
    *,
    count: int,
    today: date,
) -> None:
    foundation = FoundationService(FoundationRepository(session))
    roles = RolesService(RolesRepository(session))
    for index, (name, status) in enumerate(_EXTRA_TENANTS[:count], start=1):
        tenant = await foundation.create_tenant(
            payload={
                "name": name,
                "legal_name": f"ООО «{name}»",
                "inn_or_tin": f"DEMO-TENANT-{index:06d}",
                "contact_email": f"tenant-{index}@showcase.aurum.invalid",
                "contact_phone": f"+992 00 200 {index:02d} {index + 20:02d}",
                "legal_address": "Республика Таджикистан, демонстрационный адрес",
                "status": status,
                "drug_catalog_mode": "autonomous",
            }
        )
        owner, membership, _ownership, _role = await roles.provision_owner(
            tenant_id=tenant.id,
            email=f"owner-{index}@showcase.aurum.invalid",
            full_name=f"Владелец демо-организации {index}",
        )
        if status in {"active", "trial", "grace_period"}:
            now = _utc_at(today - timedelta(days=45 + index), 9)
            owner.status = "active"
            owner.activated_at = now
            membership.status = "active"
            membership.activated_at = now
            tenant.trial_started_at = now
            tenant.trial_ends_at = now + timedelta(days=30)
        elif status == "readonly":
            owner.status = "blocked"
            owner.blocked_at = utc_now() - timedelta(days=5)
        elif status == "archived":
            owner.status = "archived"
            owner.archived_at = utc_now() - timedelta(days=120)
            tenant.archived_at = utc_now() - timedelta(days=120)
        await session.flush()


async def seed_showcase_dataset(
    session: AsyncSession,
    *,
    profile: ShowcaseProfile,
) -> ShowcaseSummary:
    """Seed all showcase domains inside the caller's transaction."""

    rng = random.Random(f"aurum-pharma-showcase:{profile.name}:v1")
    today = utc_now().astimezone(LOCAL_TIMEZONE).date()
    tenant, owner = await prepare_main_tenant(session, profile=profile, today=today)
    branches, registers = await seed_branches_and_registers(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        profile=profile,
        today=today,
    )
    employees, cashiers_by_branch = await seed_roles_and_employees(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        branches=branches,
        today=today,
    )
    suppliers = await seed_suppliers(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        today=today,
    )
    catalog = await seed_catalog(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        profile=profile,
        today=today,
    )
    stocks, incoming_count = await seed_incoming_and_batches(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        profile=profile,
        branches=branches,
        suppliers=suppliers,
        catalog=catalog,
        today=today,
        rng=rng,
    )
    sales = generate_sales(
        tenant_id=tenant.id,
        owner_id=owner.id,
        profile=profile,
        branches=branches,
        registers=registers,
        cashiers_by_branch=cashiers_by_branch,
        catalog=catalog,
        stocks=stocks,
        today=today,
        rng=rng,
    )
    stocks_by_id = {stock.id: stock for stock in stocks}
    returns = generate_returns(
        sales=sales,
        stocks_by_id=stocks_by_id,
        today=today,
        rng=rng,
    )
    sales_count, return_count = await seed_sales_and_shifts(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        sales=sales,
        returns=returns,
        registers=registers,
        today=today,
        rng=rng,
    )
    write_off_count, supplier_return_count = await seed_inventory_adjustments(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        stocks=stocks,
        today=today,
        rng=rng,
        profile=profile,
    )
    await seed_billing(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        branch_count=len(branches),
        today=today,
    )
    await seed_notifications(
        session,
        tenant_id=tenant.id,
        owner_id=owner.id,
        employees=employees,
        today=today,
    )
    await seed_extra_tenants(session, count=profile.extra_tenants, today=today)
    await session.flush()

    return ShowcaseSummary(
        tenant_id=tenant.id,
        branches=len(branches),
        registers=len(registers),
        employees=len(employees),
        catalog_items=len(catalog),
        incoming_documents=incoming_count,
        batches=len(stocks),
        sales=sales_count,
        returns=return_count,
        write_offs=write_off_count,
        supplier_returns=supplier_return_count,
    )


async def is_showcase_complete(session: AsyncSession) -> bool:
    tenant_id = await session.scalar(
        select(Tenant.id).where(Tenant.legal_name == SHOWCASE_TENANT_LEGAL_NAME)
    )
    if tenant_id is None:
        return False
    sale_count = await session.scalar(
        select(func.count()).select_from(Sale).where(Sale.tenant_id == tenant_id)
    )
    return (sale_count or 0) > 0


async def require_clean_showcase_base(session: AsyncSession) -> None:
    """Allow only the fixed E2E base before the showcase transaction."""

    branch_count = await session.scalar(select(func.count()).select_from(Branch))
    catalog_count = await session.scalar(select(func.count()).select_from(TenantCatalog))
    sale_count = await session.scalar(select(func.count()).select_from(Sale))
    tenant_count = await session.scalar(select(func.count()).select_from(Tenant))
    user_count = await session.scalar(select(func.count()).select_from(AppUser))
    expected_base = (
        (branch_count or 0) == 0
        and (catalog_count or 0) == 0
        and (sale_count or 0) == 0
        and (tenant_count or 0) == 1
        and (user_count or 0) == 3
    )
    if not expected_base:
        raise RuntimeError(
            "Showcase seed refused: database is neither an empty E2E base nor a "
            "completed showcase dataset."
        )
