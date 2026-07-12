"""Audit triggers fire on INSERT/UPDATE/DELETE; service-level PII scrub works."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditLog
from app.domains.audit.repository import AuditRepository
from app.domains.audit.service import AuditService
from app.domains.catalog.repository import CatalogRepository
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository


async def test_insert_creates_audit_record(db_session: AsyncSession) -> None:
    service = FoundationService(FoundationRepository(db_session))
    tenant = await service.create_tenant(
        payload={
            "name": "AuditTenant",
            "contact_email": f"audit-{uuid4().hex[:6]}@aurum.tj",
        }
    )

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.table_name == "tenant",
                    AuditLog.record_id == tenant.id,
                    AuditLog.action == "INSERT",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    assert rows[0].new_values is not None
    assert rows[0].new_values.get("name") == "AuditTenant"
    assert rows[0].new_values.get("contact_email") == "***"


async def test_update_logs_only_changed_fields(db_session: AsyncSession) -> None:
    service = FoundationService(FoundationRepository(db_session))
    tenant = await service.create_tenant(
        payload={
            "name": "Before",
            "contact_email": f"upd-{uuid4().hex[:6]}@aurum.tj",
        }
    )
    await service.update_tenant(tenant.id, fields={"name": "After"})

    upd = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.table_name == "tenant",
                    AuditLog.record_id == tenant.id,
                    AuditLog.action == "UPDATE",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(upd) >= 1
    changed = upd[-1].changed_fields or {}
    assert "name" in changed
    # The updated_at column auto-updates via trigger, so it'll show too —
    # but contact_email should NOT, because it wasn't changed.
    assert "contact_email" not in changed


async def test_delete_creates_record_with_old_values(db_session: AsyncSession) -> None:
    service = FoundationService(FoundationRepository(db_session))
    tenant = await service.create_tenant(
        payload={
            "name": "Doomed",
            "contact_email": f"del-{uuid4().hex[:6]}@aurum.tj",
        }
    )
    # Use a branch to test DELETE since the foundation service doesn't
    # delete tenants; deactivating a branch isn't DELETE either. Use raw SQL.
    branch = await service.create_branch(tenant_id=tenant.id, fields={"name": "B1"})
    branch2 = await service.create_branch(tenant_id=tenant.id, fields={"name": "B2"})
    _ = branch2

    from sqlalchemy import text

    await db_session.execute(
        text("DELETE FROM branch WHERE id = :id"),
        {"id": branch.id},
    )
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.table_name == "branch",
                    AuditLog.record_id == branch.id,
                    AuditLog.action == "DELETE",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) >= 1
    assert rows[0].old_values is not None
    assert rows[0].old_values.get("name") == "B1"


async def test_trigger_redacts_sensitive_fields_at_rest(db_session: AsyncSession) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={
            "name": "RawAudit",
            "contact_email": f"raw-{uuid4().hex[:6]}@aurum.tj",
        }
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    catalog = await CatalogRepository(db_session).create_item(
        tenant_id=tenant.id,
        brand_name="Audit Mask Drug",
        inn="auditium",
        dispensing_type="otc",
        storage_type="normal",
        base_price=Decimal("7.00"),
    )
    batch = await InventoryRepository(db_session).create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=catalog.id,
        batch_number="MASK-1",
        expires_at=date(2030, 1, 1),
        purchase_price=Decimal("3.00"),
        sale_price=Decimal("5.00"),
        qty_initial=Decimal("10"),
        qty_remaining=Decimal("10"),
    )

    inserted = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "batch",
                AuditLog.record_id == batch.id,
                AuditLog.action == "INSERT",
            )
        )
    ).scalar_one()
    assert inserted.new_values is not None
    assert inserted.new_values["purchase_price"] == "***"
    assert inserted.new_values["sale_price"] != "***"

    batch.purchase_price = Decimal("4.00")
    batch.sale_price = Decimal("6.00")
    await db_session.flush()

    updated = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "batch",
                AuditLog.record_id == batch.id,
                AuditLog.action == "UPDATE",
            )
        )
    ).scalar_one()
    assert updated.old_values is not None
    assert updated.new_values is not None
    assert updated.changed_fields is not None
    assert updated.old_values["purchase_price"] == "***"
    assert updated.new_values["purchase_price"] == "***"
    assert updated.changed_fields["purchase_price"] == "***"
    assert updated.changed_fields["sale_price"] != "***"


async def test_scrub_hides_sensitive_fields() -> None:
    """The service-level scrub() must replace sensitive fields with '***'
    without losing the rest of the payload."""
    fake = AuditLog(
        id=uuid4(),
        tenant_id=None,
        user_id=None,
        action="UPDATE",
        table_name="app_user",
        record_id=uuid4(),
        old_values={
            "email": "x@aurum.tj",
            "password_hash": "$2b$12$abcdef",
            "totp_secret": "JBSWY3DPEHPK3PXP",
            "profile": {"phone": "+992000000000", "contact_email": "p@aurum.tj"},
        },
        new_values={
            "email": "x@aurum.tj",
            "password_hash": "$2b$12$newhash",
            "purchase_price": "3.00",
            "doctor_license": "D-123",
            "prescription_number": "RX-SECRET",
            "notes": "sensitive patient note",
        },
        changed_fields={"password_hash": "$2b$12$newhash"},
    )
    fake.created_at = __import__("datetime").datetime.utcnow()

    scrubbed = AuditService.scrub(fake)
    assert scrubbed["old_values"]["password_hash"] == "***"
    assert scrubbed["old_values"]["totp_secret"] == "***"
    assert scrubbed["new_values"]["password_hash"] == "***"
    assert scrubbed["new_values"]["email"] == "***"
    assert scrubbed["new_values"]["purchase_price"] == "***"
    assert scrubbed["new_values"]["doctor_license"] == "***"
    assert scrubbed["new_values"]["prescription_number"] == "***"
    assert scrubbed["new_values"]["notes"] == "***"
    assert scrubbed["old_values"]["profile"]["phone"] == "***"
    assert scrubbed["old_values"]["profile"]["contact_email"] == "***"
    assert scrubbed["changed_fields"]["password_hash"] == "***"


async def test_service_search_filters_by_tenant(db_session: AsyncSession) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    audit = AuditService(AuditRepository(db_session))
    t1 = await foundation.create_tenant(
        payload={"name": "T1", "contact_email": f"t1-{uuid4().hex[:6]}@aurum.tj"}
    )
    t2 = await foundation.create_tenant(
        payload={"name": "T2", "contact_email": f"t2-{uuid4().hex[:6]}@aurum.tj"}
    )

    rows_t1, total_t1 = await audit.search(tenant_id=t1.id)
    rows_t2, total_t2 = await audit.search(tenant_id=t2.id)

    assert total_t1 >= 1
    assert total_t2 >= 1
    assert all(r.tenant_id == t1.id for r in rows_t1)
    assert all(r.tenant_id == t2.id for r in rows_t2)


async def test_explicit_log_view_writes_row(db_session: AsyncSession) -> None:
    service = AuditService(AuditRepository(db_session))
    rec_id = uuid4()

    # user_id has a FK to app_user; passing None is fine (it's nullable).
    entry = await service.log_view(
        tenant_id=None,
        user_id=None,
        table_name="batch",
        record_id=rec_id,
        metadata={"reason": "show_purchase_price", "purchase_price": "3.00"},
    )
    assert entry.action == "VIEW"
    assert entry.record_id == rec_id

    found = (
        await db_session.execute(select(AuditLog).where(AuditLog.id == entry.id))
    ).scalar_one_or_none()
    assert found is not None
    assert found.action == "VIEW"
    assert found.metadata_json is not None
    assert found.metadata_json["purchase_price"] == "***"
