"""Audit triggers fire on INSERT/UPDATE/DELETE; service-level PII scrub works."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditLog
from app.domains.audit.repository import AuditRepository
from app.domains.audit.service import AuditService
from app.domains.catalog.repository import CatalogRepository
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.repository import InventoryRepository
from app.domains.suppliers.repository import SuppliersRepository


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
        text("DELETE FROM sync_stream WHERE branch_id = :id"),
        {"id": branch.id},
    )
    await db_session.execute(
        text("DELETE FROM sync_writer_epoch WHERE branch_id = :id"),
        {"id": branch.id},
    )
    await db_session.execute(
        text("DELETE FROM sync_node WHERE branch_id = :id"),
        {"id": branch.id},
    )
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


async def test_database_redacts_contextual_purchase_totals(db_session: AsyncSession) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={
            "name": "Contextual audit",
            "contact_email": f"contextual-{uuid4().hex[:6]}@aurum.tj",
        }
    )
    branch = await foundation.create_branch(tenant_id=tenant.id, fields={"name": "Main"})
    supplier = await SuppliersRepository(db_session).create_supplier(
        tenant_id=tenant.id,
        name="Audit supplier",
    )
    document = await IncomingService(IncomingRepository(db_session)).create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
            "document_number": "IN-1",
        },
    )
    entry = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.table_name == "incoming_document",
                AuditLog.record_id == document.id,
                AuditLog.action == "INSERT",
            )
        )
    ).scalar_one()

    assert entry.new_values is not None
    assert entry.new_values["total_amount"] == "***"
    assert entry.new_values["document_number"] == "IN-1"


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
            "receipt_snapshot": {"receipt_number": "SECRET-1"},
            "notes": "sensitive patient note",
            "metadata": {
                "cashier_name": "Private cashier",
                "comment": "sensitive nested comment",
                "items": [{"comment": "another sensitive comment", "code": "SAFE"}],
            },
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
    assert scrubbed["new_values"]["receipt_snapshot"] == "***"
    assert scrubbed["new_values"]["notes"] == "***"
    assert scrubbed["new_values"]["metadata"]["cashier_name"] == "***"
    assert scrubbed["new_values"]["metadata"]["comment"] == "***"
    assert scrubbed["new_values"]["metadata"]["items"][0]["comment"] == "***"
    assert scrubbed["new_values"]["metadata"]["items"][0]["code"] == "SAFE"
    assert scrubbed["old_values"]["profile"]["phone"] == "***"
    assert scrubbed["old_values"]["profile"]["contact_email"] == "***"
    assert scrubbed["changed_fields"]["password_hash"] == "***"


async def test_scrub_hides_contextual_purchase_totals() -> None:
    incoming = AuditLog(
        id=uuid4(),
        tenant_id=None,
        user_id=None,
        action="INSERT",
        table_name="incoming_document",
        record_id=uuid4(),
        new_values={"total_amount": "120.00", "document_number": "IN-1"},
    )
    incoming.created_at = __import__("datetime").datetime.utcnow()
    supplier_return = AuditLog(
        id=uuid4(),
        tenant_id=None,
        user_id=None,
        action="INSERT",
        table_name="supplier_return",
        record_id=uuid4(),
        new_values={"amount": "12.00", "qty": "3.000"},
    )
    supplier_return.created_at = __import__("datetime").datetime.utcnow()

    incoming_payload = AuditService.scrub(incoming)
    return_payload = AuditService.scrub(supplier_return)

    assert incoming_payload["new_values"]["total_amount"] == "***"
    assert incoming_payload["new_values"]["document_number"] == "IN-1"
    assert return_payload["new_values"]["amount"] == "***"
    assert return_payload["new_values"]["qty"] == "3.000"


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


async def test_explicit_authorization_denial_is_append_only_and_redacted(
    db_session: AsyncSession,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={
            "name": "Authorization audit tenant",
            "contact_email": f"authorization-audit-{uuid4().hex[:6]}@aurum.test",
        }
    )
    user_id = uuid4()
    await db_session.execute(
        text("""
            INSERT INTO public.app_user (id, email, full_name, home_tenant_id)
            VALUES (:id, :email, 'Audit actor', :tenant_id)
            """),
        {
            "id": user_id,
            "email": f"authorization-actor-{uuid4().hex[:6]}@aurum.test",
            "tenant_id": tenant.id,
        },
    )
    service = AuditService(AuditRepository(db_session))

    entry = await service.log_authorization_denied(
        tenant_id=tenant.id,
        user_id=user_id,
        metadata={
            "result": "denied",
            "reason": "self_assignment_denied",
            "method": "POST",
            "path": "/api/v1/users/{user_id}/assignments",
            "email": "must-not-appear@aurum.test",
        },
    )

    assert entry.action == "AUTHORIZATION_DENIED"
    assert entry.table_name == "authorization_policy"
    assert entry.record_id is None
    assert entry.metadata_json is not None
    assert entry.metadata_json["reason"] == "self_assignment_denied"
    assert entry.metadata_json["email"] == "***"
