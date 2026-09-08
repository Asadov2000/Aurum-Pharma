"""Incomplete input survives reload; only explicit, atomic conversion creates stock rows."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch, Tenant
from app.domains.incoming.draft_service import IncomingDraftService
from app.domains.incoming.models import IncomingDocument, IncomingDraftRow, IncomingItem
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import IncomingDraftFields
from app.domains.incoming.service import IncomingService
from app.domains.suppliers.models import Supplier
from app.main import app

type Scaffold = Callable[[], Awaitable[tuple[Tenant, Branch, TenantCatalog, Supplier]]]


def _fields(catalog_id: UUID | None = None, *, qty: str = "12,") -> IncomingDraftFields:
    return IncomingDraftFields(
        catalog_id=str(catalog_id) if catalog_id else "",
        batch_number="  incomplete batch  ",
        manufactured_at="",
        expires_at=(date.today() + timedelta(days=365)).isoformat(),
        qty=qty,
        purchase_price="5,20",
        sale_price="7.00",
    )


async def _document(
    session: AsyncSession, scaffold: Scaffold
) -> tuple[IncomingService, IncomingDocument, TenantCatalog]:
    tenant, branch, catalog, supplier = await scaffold()
    service = IncomingService(IncomingRepository(session))
    document = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    return service, document, catalog


async def _save(
    drafts: IncomingDraftService, document: IncomingDocument, fields: IncomingDraftFields
) -> IncomingDraftRow:
    return await drafts.save(
        document.id, uuid4(), expected_revision=0, fields=fields, actor_id=uuid4()
    )


async def test_reload_preserves_raw_fields_and_retry_does_not_write_audit(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    service, document, _ = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    fields = _fields()
    row = await _save(drafts, document, fields)
    row_id, tenant_id = row.id, document.tenant_id
    retry = await drafts.save(
        document.id, row_id, expected_revision=0, fields=fields, actor_id=uuid4()
    )
    assert retry.revision == 1
    db_session.expire(row)
    loaded = await service.repo.get_draft_row(tenant_id, row_id)
    assert loaded is not None and loaded.fields == fields.as_fields()
    assert await service.repo.list_items(document.id) == []
    events = (
        await db_session.execute(
            text(
                "SELECT new_values, changed_fields FROM audit_log "
                "WHERE tenant_id=:tenant AND table_name='incoming_draft_row' AND record_id=:id"
            ),
            {"tenant": tenant_id, "id": row_id},
        )
    ).all()
    assert len(events) == 1
    assert events[0].new_values["fields"] == "***"
    assert "5,20" not in str(events)


async def test_stale_revision_cannot_overwrite_new_input(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(catalog.id))
    changed = _fields(catalog.id, qty="25")
    saved = await drafts.save(
        document.id, row.id, expected_revision=1, fields=changed, actor_id=uuid4()
    )
    assert saved.revision == 2
    with pytest.raises(ConflictError) as error:
        await drafts.save(
            document.id,
            row.id,
            expected_revision=1,
            fields=_fields(catalog.id, qty="30"),
            actor_id=uuid4(),
        )
    assert error.value.details == {"reason": "draft_revision_conflict", "current_revision": 2}
    repeated = await drafts.save(
        document.id, row.id, expected_revision=1, fields=changed, actor_id=uuid4()
    )
    assert repeated.revision == 2 and repeated.fields["qty"] == "25"


async def test_discard_tombstone_rejects_delayed_initial_save(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, _ = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    fields = _fields()
    row = await _save(drafts, document, fields)
    await drafts.discard(document.id, row.id, expected_revision=1, actor_id=uuid4())
    await drafts.discard(document.id, row.id, expected_revision=1, actor_id=uuid4())
    with pytest.raises(ConflictError):
        await drafts.save(document.id, row.id, expected_revision=0, fields=fields, actor_id=uuid4())
    assert await service.repo.list_draft_rows(document.id) == []
    assert row.status == "discarded" and row.revision == 2


async def test_commit_retry_creates_one_item_and_survives_finalization(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(catalog.id, qty="12,5"))
    first = await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    second = await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    assert first.id == second.id and first.operation_id == row.id
    assert len(await service.repo.list_items(document.id)) == 1
    await service.accept(document.id)
    repeated = await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    assert repeated.id == first.id
    with pytest.raises(BusinessRuleError):
        await drafts.save(
            document.id, uuid4(), expected_revision=0, fields=_fields(), actor_id=uuid4()
        )
    with pytest.raises(BusinessRuleError):
        await drafts.discard(document.id, row.id, expected_revision=2, actor_id=uuid4())


async def test_deleted_committed_item_is_not_recreated_on_retry(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(catalog.id, qty="1"))
    item = await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    await service.delete_item(document.id, item.id)
    with pytest.raises(NotFoundError):
        await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    assert await service.repo.list_items(document.id) == []


async def test_invalid_commit_is_safe_and_does_not_create_item(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(catalog.id))
    with pytest.raises(BusinessRuleError) as error:
        await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    assert error.value.details == {"reason": "draft_fields_invalid", "fields": ["qty"]}
    assert row.status == "pending" and row.revision == 1
    assert await service.repo.list_items(document.id) == []


async def test_conversion_failure_rolls_back_item_and_total(
    db_session: AsyncSession,
    scaffold: Scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(catalog.id, qty="3"))
    row_id = row.id

    async def fail_after_item_written(changed: IncomingDraftRow) -> IncomingDraftRow:
        assert changed.status == "committed"
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(IncomingItem)
                .where(IncomingItem.document_id == document.id)
            )
            == 1
        )
        raise RuntimeError("Injected persistence failure")

    monkeypatch.setattr(service.repo, "persist_draft_row", fail_after_item_written)
    with pytest.raises(RuntimeError, match="Injected persistence failure"):
        await drafts.commit(document.id, row_id, expected_revision=1, actor_id=uuid4())
    await db_session.refresh(document)
    restored = await service.repo.get_draft_row(document.tenant_id, row_id)
    assert restored is not None and restored.status == "pending" and restored.revision == 1
    assert document.total_amount == 0
    assert await service.repo.list_items(document.id) == []


async def test_pending_rows_block_acceptance_and_branch_scope(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    completed = await _save(drafts, document, _fields(catalog.id, qty="1"))
    await drafts.commit(document.id, completed.id, expected_revision=1, actor_id=uuid4())
    pending = await _save(drafts, document, _fields())
    with pytest.raises(BusinessRuleError) as error:
        await service.accept(document.id)
    assert error.value.details["reason"] == "pending_draft_rows"
    with pytest.raises(PermissionDeniedError):
        await drafts.save(
            document.id,
            pending.id,
            expected_revision=1,
            fields=_fields(),
            actor_id=uuid4(),
            allowed_branch_ids=set(),
        )
    with pytest.raises(PermissionDeniedError):
        await drafts.list_rows(document.id, allowed_branch_ids=set())
    await drafts.discard(document.id, pending.id, expected_revision=1, actor_id=uuid4())
    assert (await service.accept(document.id)).status == "accepted"


async def test_foreign_catalog_never_resolves_or_commits(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, _ = await _document(db_session, scaffold)
    _, _, foreign_catalog, _ = await scaffold()
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields(foreign_catalog.id, qty="1"))
    assert await drafts.catalog_names(document.tenant_id, [row]) == {}
    with pytest.raises(NotFoundError):
        await drafts.commit(document.id, row.id, expected_revision=1, actor_id=uuid4())
    assert await service.repo.list_items(document.id) == []


async def test_http_restore_cost_redaction_and_validation_privacy(
    client: AsyncClient,
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=document.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"incoming.view", "incoming.create"},
        permission_scopes={
            key: frozenset({document.branch_id}) for key in ("incoming.view", "incoming.create")
        },
    )

    async def db_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def user_override() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[current_user] = user_override
    path = f"/api/v1/incoming/{document.id}/draft-rows"
    row_id = uuid4()
    try:
        saved = await client.put(
            f"{path}/{row_id}",
            json={"expected_revision": 0, "fields": _fields(catalog.id).as_fields()},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["catalog_name"] == catalog.brand_name
        loaded = await client.get(path)
        assert loaded.status_code == 200 and loaded.json()[0]["fields"]["qty"] == "12,"
        actor.permissions.remove("incoming.create")
        actor.permission_scopes.pop("incoming.create")
        redacted = await client.get(path)
        assert redacted.json()[0]["fields"]["purchase_price"] is None
        denied = await client.put(
            f"{path}/{uuid4()}", json={"expected_revision": 0, "fields": _fields().as_fields()}
        )
        assert denied.status_code == 403
        actor.permissions.add("incoming.create")
        actor.permission_scopes["incoming.create"] = frozenset({document.branch_id})
        malformed = _fields().as_fields() | {"purchase_price": "private-cost-" * 10}
        invalid = await client.put(
            f"{path}/{uuid4()}", json={"expected_revision": 0, "fields": malformed}
        )
        assert invalid.status_code == 422
        assert "private-cost" not in invalid.text
        assert invalid.json()["error"]["details"]["reason"] == "draft_request_invalid"
        stale = await client.put(
            f"{path}/{row_id}",
            json={"expected_revision": 0, "fields": _fields(catalog.id, qty="55").as_fields()},
        )
        assert stale.status_code == 409 and "5,20" not in stale.text
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_database_guards_prevent_terminal_mutation_and_physical_delete(
    db_session: AsyncSession,
    scaffold: Scaffold,
) -> None:
    service, document, _ = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    row = await _save(drafts, document, _fields())
    row_id = row.id
    await service.reject(document.id)
    for statement in (
        "UPDATE incoming_draft_row SET revision=revision+1 WHERE id=:id",
        "DELETE FROM incoming_draft_row WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(text(statement), {"id": row_id})
    stored = await service.repo.get_draft_row(document.tenant_id, row_id)
    assert stored is not None and stored.revision == 1


async def test_database_pending_guard_rolls_back_attempted_acceptance(
    db_session: AsyncSession,
    scaffold: Scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, document, catalog = await _document(db_session, scaffold)
    drafts = IncomingDraftService(service)
    ready = await _save(drafts, document, _fields(catalog.id, qty="1"))
    await drafts.commit(document.id, ready.id, expected_revision=1, actor_id=uuid4())
    pending = await _save(drafts, document, _fields())
    pending_id = pending.id
    document_id = document.id

    async def missed_pending_rows(_document_id: UUID) -> int:
        return 0

    monkeypatch.setattr(service.repo, "count_draft_rows", missed_pending_rows)
    with pytest.raises(DBAPIError, match="Pending draft rows prevent acceptance"):
        async with db_session.begin_nested():
            await service.accept(document_id)
    await db_session.refresh(document)
    restored = await service.repo.get_draft_row(document.tenant_id, pending_id)
    assert document.status == "draft" and restored is not None and restored.status == "pending"
    assert all(item.created_batch_id is None for item in await service.repo.list_items(document_id))
