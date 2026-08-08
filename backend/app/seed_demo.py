"""Demo data seeder — NOT an Alembic migration (demo data must never reach a
production migration). Idempotent and tenant-parameterised so it can re-fill the
demo pharmacy or seed a brand-new one.

Run inside the backend container:

    docker compose exec backend python -m app.seed_demo

What it does (all money is Decimal / NUMERIC, currency TJS):
  1. master_catalog  — ~190 real medicines (closes the empty-catalogue risk).
     Insert-missing, so re-runs never duplicate.
  2. The demo tenant — renamed to «Аптека Шифо», status set to active so POS
     sales count (a 'setup' tenant marks sales is_test and they'd be excluded).
  3. tenant_catalog  — its own copy of every master drug (master_id linked),
     realistic сомони retail price, a valid EAN-13 barcode each.
  4. Stock           — 1–3 batches per item via the real incoming path
     (create_batch + an 'incoming' batch_movement; a DB trigger raises
     qty_remaining), markup ~20–40%, expiries spread (some near, some far),
     some deliberately low remaining.
  5. Sales           — a small recent history through the real POS service
     (open shift → draft → FEFO add_item → cash/card payment → complete), OTC
     items only (prescription items would demand a Rx log), some back-dated so
     reports/dashboard aren't empty.

Idempotency: master is insert-missing; the tenant catalog/stock/sales block is
guarded by a sentinel (master-linked tenant_catalog rows). Re-running is a
no-op for the tenant block. SEED_DEMO_FORCE=1 is accepted only before the
tenant has finalized receipts; immutable financial history is never erased.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SupportSessionLocal
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.catalog.models import Barcode, MasterCatalog, TenantCatalog
from app.domains.foundation.models import Branch, Register, Tenant
from app.domains.inventory.models import Batch, BatchMovement
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.seed_demo_data import DRUGS

DEMO_NAME = "Аптека Шифо"
OLD_DEMO_NAMES = ("Demo Pharmacy", DEMO_NAME)
CASHIER_EMAIL = "owner@aurum.tj"
SEED_BRANCH_NAME = "Аптека Шифо — Центральная"
SEED_REGISTER_NAME = "Касса №1"

_RND = random.Random(42)  # deterministic prices/qty/expiries across runs


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fixed_clock(value: datetime) -> Callable[[], datetime]:
    def now() -> datetime:
        return value

    return now


def _ean13(seq: int) -> str:
    """Valid EAN-13 from a sequence number: '200' internal prefix + 9 digits +
    checksum."""
    base = f"200{seq:09d}"  # 12 digits
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base))
    check = (10 - total % 10) % 10
    return base + str(check)


# ---------------------------------------------------------------------------
# master_catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MasterRow:
    brand_name: str
    inn: str
    manufacturer: str
    form: str
    dosage: str
    pack_size: str
    atx_code: str
    dispensing_type: str
    storage_type: str
    category: str
    price: Decimal


def _master_rows() -> list[MasterRow]:
    rows: list[MasterRow] = []
    for drug in DRUGS:
        for dosage, pack, price in drug["variants"]:
            rows.append(
                MasterRow(
                    brand_name=drug["brand"],
                    inn=drug["inn"],
                    manufacturer=drug["manufacturer"],
                    form=drug["form"],
                    dosage=dosage,
                    pack_size=pack,
                    atx_code=drug["atx"],
                    dispensing_type=drug["dispensing"],
                    storage_type=drug["storage"],
                    category=drug["category"],
                    price=Decimal(price),
                )
            )
    return rows


def _key(r: MasterRow) -> tuple[str, str, str, str]:
    return (r.brand_name, r.dosage, r.form, r.manufacturer)


async def _seed_master(session: AsyncSession) -> dict[tuple[str, str, str, str], UUID]:
    existing: dict[tuple[str, str, str, str], UUID] = {}
    for m in (await session.execute(select(MasterCatalog))).scalars().all():
        existing[(m.brand_name, m.dosage or "", m.form or "", m.manufacturer or "")] = m.id

    created = 0
    for r in _master_rows():
        if _key(r) in existing:
            continue
        m = MasterCatalog(
            brand_name=r.brand_name,
            inn=r.inn,
            manufacturer=r.manufacturer,
            form=r.form,
            dosage=r.dosage,
            pack_size=r.pack_size,
            atx_code=r.atx_code,
            dispensing_type=r.dispensing_type,
            storage_type=r.storage_type,
        )
        session.add(m)
        await session.flush()
        existing[_key(r)] = m.id
        created += 1
    print(f"master_catalog: +{created} (всего {len(existing)})")
    return existing


# ---------------------------------------------------------------------------
# tenant
# ---------------------------------------------------------------------------


async def _get_demo(session: AsyncSession) -> Tenant:
    tenant = (
        (await session.execute(select(Tenant).where(Tenant.name.in_(OLD_DEMO_NAMES))))
        .scalars()
        .first()
    )
    if tenant is None:
        raise SystemExit("Демо-тенант не найден (ожидался 'Demo Pharmacy' / 'Аптека Шифо').")
    return tenant


async def _clean_demo(session: AsyncSession, tenant_id: UUID) -> None:
    """Reset a demo only while it has no immutable financial or stock history."""
    finalized_count = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM sale "
                    "WHERE tenant_id = :tenant_id AND status <> 'draft'"
                ),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
    )
    if finalized_count:
        raise RuntimeError(
            "Demo tenant has finalized receipts; recreate the disposable demo "
            "database instead of deleting financial history"
        )

    movement_count = int(
        (
            await session.execute(
                text("SELECT count(*) FROM batch_movement WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
    )
    if movement_count:
        raise RuntimeError(
            "Demo tenant has immutable stock movements; recreate the disposable demo "
            "database instead of deleting inventory history"
        )

    for table in (
        "sale_payment",
        "sale_item",
        "prescription_log",
        "sale",
        "write_off",
        # incoming_item.created_batch_id → batch, so incoming rows go first.
        "incoming_item",
        "supplier_return",
        "incoming_document",
        "batch",
        "barcode",
        "catalog_import_job",
        "tenant_catalog",
        "shift",
    ):
        await session.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})


async def _ensure_branch_register(
    session: AsyncSession, tenant_id: UUID, actor_id: UUID | None
) -> tuple[Branch, Register]:
    branch = (
        (
            await session.execute(
                select(Branch).where(Branch.tenant_id == tenant_id, Branch.name == SEED_BRANCH_NAME)
            )
        )
        .scalars()
        .first()
    )
    if branch is None:
        branch = Branch(
            tenant_id=tenant_id,
            name=SEED_BRANCH_NAME,
            address="г. Душанбе, пр. Рудаки, 25",
            branch_type="pharmacy",
            license_number="ФА-2024-0042",
            license_expires_at=date.today() + timedelta(days=120),
            created_by=actor_id,
        )
        session.add(branch)
        await session.flush()

    register = (
        (
            await session.execute(
                select(Register).where(
                    Register.tenant_id == tenant_id,
                    Register.branch_id == branch.id,
                    Register.name == SEED_REGISTER_NAME,
                )
            )
        )
        .scalars()
        .first()
    )
    if register is None:
        register = Register(
            tenant_id=tenant_id,
            branch_id=branch.id,
            name=SEED_REGISTER_NAME,
            created_by=actor_id,
        )
        session.add(register)
        await session.flush()
    return branch, register


# ---------------------------------------------------------------------------
# tenant_catalog + barcodes
# ---------------------------------------------------------------------------


async def _seed_tenant_catalog(
    session: AsyncSession,
    tenant_id: UUID,
    master_map: dict[tuple[str, str, str, str], UUID],
    actor_id: UUID | None,
) -> list[TenantCatalog]:
    items: list[TenantCatalog] = []
    seq = 1
    for r in _master_rows():
        item = TenantCatalog(
            tenant_id=tenant_id,
            master_id=master_map[_key(r)],
            brand_name=r.brand_name,
            inn=r.inn,
            manufacturer=r.manufacturer,
            form=r.form,
            dosage=r.dosage,
            pack_size=r.pack_size,
            atx_code=r.atx_code,
            dispensing_type=r.dispensing_type,
            storage_type=r.storage_type,
            category=r.category,
            base_price=_money(r.price),
            currency="TJS",
            is_active=True,
            created_by=actor_id,
        )
        session.add(item)
        await session.flush()
        session.add(
            Barcode(
                tenant_id=tenant_id,
                catalog_id=item.id,
                code=_ean13(seq),
                code_type="ean13",
            )
        )
        items.append(item)
        seq += 1
    await session.flush()
    print(f"tenant_catalog: +{len(items)} позиций (+{len(items)} штрихкодов)")
    return items


# ---------------------------------------------------------------------------
# stock (batches)
# ---------------------------------------------------------------------------


async def _seed_batches(
    session: AsyncSession,
    tenant_id: UUID,
    branch_id: UUID,
    items: list[TenantCatalog],
    actor_id: UUID | None,
) -> int:
    today = date.today()
    n = 0
    for idx, item in enumerate(items):
        sale_base = item.base_price or Decimal("10.00")
        for b in range(_RND.randint(1, 3)):
            markup = Decimal(str(_RND.choice([1.2, 1.25, 1.3, 1.35, 1.4])))
            sale_price = _money(sale_base * Decimal(str(_RND.choice([0.98, 1.0, 1.02, 1.05]))))
            purchase_price = _money(sale_price / markup)
            # Expiry spread: ~20% near (15–45d), ~30% mid (3–8m), rest far (8–24m).
            roll = _RND.random()
            if roll < 0.20:
                exp = today + timedelta(days=_RND.randint(15, 45))
            elif roll < 0.50:
                exp = today + timedelta(days=_RND.randint(90, 240))
            else:
                exp = today + timedelta(days=_RND.randint(240, 720))
            # Quantity: ~25% low remaining, rest healthy.
            qty = (
                Decimal(_RND.randint(2, 8))
                if _RND.random() < 0.25
                else Decimal(_RND.randint(20, 300))
            )
            batch = Batch(
                tenant_id=tenant_id,
                branch_id=branch_id,
                catalog_id=item.id,
                batch_number=f"L{idx + 1:04d}-{b + 1}",
                expires_at=exp,
                purchase_price=purchase_price,
                sale_price=sale_price,
                currency="TJS",
                qty_initial=qty,
                qty_remaining=Decimal("0"),  # raised by the movement trigger
                created_by=actor_id,
            )
            session.add(batch)
            await session.flush()
            session.add(
                BatchMovement(
                    tenant_id=tenant_id,
                    batch_id=batch.id,
                    movement_type="incoming",
                    qty_delta=qty,
                    notes="Демо-приход",
                    created_by=actor_id,
                )
            )
            await session.flush()
            n += 1
    print(f"batch: +{n} партий")
    return n


# ---------------------------------------------------------------------------
# sales (recent history through the real POS flow)
# ---------------------------------------------------------------------------


async def _seed_sales(
    session: AsyncSession,
    tenant: Tenant,
    register_id: UUID,
    items: list[TenantCatalog],
    cashier_id: UUID,
    target_count: int = 55,
) -> int:
    pos = POSService(POSRepository(session))
    otc = [i for i in items if i.dispensing_type == "otc"]
    if not otc:
        return 0

    shift = await pos.open_shift(
        tenant_id=tenant.id,
        register_id=register_id,
        opened_by_user_id=cashier_id,
        opening_cash=Decimal("200.00"),
    )
    _ = shift
    done = 0
    for _i in range(target_count):
        completed_at = utc_now()
        if _RND.random() < 0.70:
            completed_at -= timedelta(
                days=_RND.randint(1, 14),
                hours=_RND.randint(0, 8),
                minutes=_RND.randint(0, 59),
            )
        sale = await pos.create_sale(
            tenant_id=tenant.id, register_id=register_id, cashier_user_id=cashier_id
        )
        await pos.repo.update_sale(
            sale,
            created_at=completed_at - timedelta(minutes=_RND.randint(1, 7)),
        )
        added = 0
        for cat in _RND.sample(otc, k=_RND.randint(1, 3)):
            qty = Decimal(_RND.randint(1, 2))
            try:
                await pos.add_item(sale_id=sale.id, catalog_id=cat.id, qty=qty)
                added += 1
            except Exception:  # out of stock for this pick; just skip the item
                continue
        if added == 0:
            await session.execute(text("DELETE FROM sale WHERE id = :s"), {"s": sale.id})
            continue
        fresh = await pos.get_sale(sale.id)
        method = _RND.choice(["cash", "cash", "card", "qr"])
        metadata = {"external_confirmed": True} if method in {"card", "qr"} else None
        await pos.add_payment(
            sale_id=sale.id,
            payment_method=method,
            amount=fresh.total_amount,
            metadata=metadata,
        )
        timed_pos = POSService(POSRepository(session), now=_fixed_clock(completed_at))
        await timed_pos.complete(sale_id=sale.id)
        done += 1
    print(f"sale: +{done} продаж (смена открыта)")
    return done


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def _count(session: AsyncSession, sql: str, params: dict[str, object]) -> int:
    return int((await session.execute(text(sql), params)).scalar_one())


async def main() -> None:
    force = os.getenv("SEED_DEMO_FORCE") == "1"
    async with SupportSessionLocal() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            tenant = await _get_demo(session)
            tenant.name = DEMO_NAME
            tenant.status = "active"
            await session.flush()

            master_map = await _seed_master(session)

            linked = await _count(
                session,
                "SELECT count(*) FROM tenant_catalog "
                "WHERE tenant_id = :t AND master_id IS NOT NULL AND deleted_at IS NULL",
                {"t": tenant.id},
            )
            if linked > 0 and not force:
                print(
                    f"Демо уже засеяно ({linked} позиций с master_id). "
                    "Пропускаю. SEED_DEMO_FORCE=1 — пересоздать."
                )
                return

            cashier = (
                (await session.execute(select(AppUser).where(AppUser.email_lower == CASHIER_EMAIL)))
                .scalars()
                .first()
            )
            actor_id = cashier.id if cashier else None

            await _clean_demo(session, tenant.id)
            branch, register = await _ensure_branch_register(session, tenant.id, actor_id)
            items = await _seed_tenant_catalog(session, tenant.id, master_map, actor_id)
            n_batches = await _seed_batches(session, tenant.id, branch.id, items, actor_id)

            n_sales = 0
            if cashier is not None:
                n_sales = await _seed_sales(session, tenant, register.id, items, cashier.id)
            else:
                print("sale: пропущено (нет пользователя owner@aurum.tj для кассира)")

            print(
                "\nГОТОВО: "
                f"тенант «{tenant.name}» (active), "
                f"позиций {len(items)}, партий {n_batches}, продаж {n_sales}, "
                f"филиал «{branch.name}», касса «{register.name}»."
            )


if __name__ == "__main__":
    asyncio.run(main())
