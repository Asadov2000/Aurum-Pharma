"""Electronic option discovery remains scoped and does not flush shared facts."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.foundation.models import TenantSettings
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundOptions
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_electronic_refund_preview_repository import (
    MixedElectronicRefundPreviewRepository,
)
from app.domains.pos.mixed_electronic_refund_preview_service import (
    MixedElectronicRefundPreviewService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_electronic_refund_preview import financial_facts, original
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def read_options(session: AsyncSession, source: MixedCheckoutResultV4) -> MixedRefundOptions:
    return await MixedElectronicRefundPreviewService(
        MixedElectronicRefundPreviewRepository(session)
    ).options(
        tenant_id=source.tenant_id,
        parent_sale_id=source.sale_id,
        actor_id=runtime(session).actor_id,
        allowed_branch_ids={source.branch_id},
    )


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "card", "qr")])
async def test_real_options_preserve_scope_and_do_not_make_financial_changes(
    db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    source = await original(db_session, methods)
    before = await financial_facts(db_session)
    result = await read_options(db_session, source)
    assert (result.parent_sale_id, result.branch_id, result.register_id) == (
        source.sale_id,
        source.branch_id,
        source.register_id,
    )
    assert [line.line_id for line in result.lines] == [line.id for line in source.lines]
    assert {line.inventory_kind for line in result.lines} == {"legacy_pack", "physical_unit"}
    assert await read_options(db_session, source) == result
    assert await financial_facts(db_session) == before


async def test_closed_shift_options_preserve_pending_orm_edits(db_session: AsyncSession) -> None:
    source = await original(db_session, ("cash", "card", "qr"))
    first = await read_options(db_session, source)
    await close_shift(db_session, source.shift_id)
    before = await financial_facts(db_session)
    marker = await db_session.get(MixedCashCheckout, (source.tenant_id, source.operation_id))
    assert marker is not None
    original_hash = marker.request_hash
    marker.request_hash = "f" * 64
    assert marker in db_session.dirty
    assert await read_options(db_session, source) == first
    assert marker.request_hash == "f" * 64 and marker in db_session.dirty
    marker.request_hash = original_hash
    assert await financial_facts(db_session) == before


async def test_options_keep_pending_tenant_settings_and_read_persisted_policy(
    db_session: AsyncSession,
) -> None:
    source = await original(db_session, ("card",))
    settings = await db_session.get(TenantSettings, source.tenant_id)
    assert settings is not None
    persisted_mode = settings.refund_reason_mode
    pending_mode = "required" if persisted_mode != "required" else "optional"
    settings.refund_reason_mode = pending_mode
    assert settings in db_session.dirty
    result = await read_options(db_session, source)
    assert result.reason_mode == persisted_mode
    assert settings.refund_reason_mode == pending_mode
    assert settings in db_session.dirty
    assert await db_session.get(TenantSettings, source.tenant_id) is settings
    settings.refund_reason_mode = persisted_mode


@pytest.mark.parametrize("denial", ["tenant", "actor", "branch"])
async def test_real_options_deny_foreign_scope_without_changes(
    db_session: AsyncSession, denial: str
) -> None:
    source = await original(db_session, ("card",))
    before = await financial_facts(db_session)
    with pytest.raises((BusinessRuleError, PermissionDeniedError, NotFoundError)):
        await MixedElectronicRefundPreviewService(
            MixedElectronicRefundPreviewRepository(db_session)
        ).options(
            tenant_id=uuid4() if denial == "tenant" else source.tenant_id,
            parent_sale_id=source.sale_id,
            actor_id=uuid4() if denial == "actor" else runtime(db_session).actor_id,
            allowed_branch_ids={uuid4()} if denial == "branch" else {source.branch_id},
        )
    assert await financial_facts(db_session) == before
