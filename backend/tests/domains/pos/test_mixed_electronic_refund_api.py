"""HTTP adapters preserve authorization, payload boundaries and saved recovery."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.core.errors import AurumError
from app.domains.pos import mixed_electronic_refund_activation_router as activation
from app.domains.pos import mixed_electronic_refund_binding_router as binding
from app.domains.pos import mixed_electronic_refund_outcome_router as outcome
from app.domains.pos import mixed_electronic_refund_posting_router as posting
from app.domains.pos import mixed_electronic_refund_review_router as review
from app.domains.pos.mixed_electronic_refund_activation_schemas import (
    SavedMixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_binding_schemas import MixedElectronicRefundBindingRead
from app.domains.pos.mixed_electronic_refund_intent_schemas import SavedMixedElectronicRefundIntent
from app.domains.pos.mixed_electronic_refund_outcome_schemas import (
    ReconciledMixedElectronicRefund,
)
from tests.domains.pos.test_mixed_electronic_refund_intent_service import Review
from tests.domains.pos.test_mixed_electronic_refund_posting_contracts import posted


@dataclass
class Case:
    method: str
    path: str
    body: dict[str, object] | None
    call: str


@dataclass
class Api:
    client: AsyncClient
    actor: CurrentUser
    session: MagicMock
    parent: UUID
    operation: UUID
    branch: UUID
    calls: dict[str, AsyncMock]
    cases: tuple[Case, ...]


@pytest.fixture
async def mixed_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Api]:
    facts = Review(monkeypatch)
    saved = SavedMixedElectronicRefundIntent(
        operation_id=facts.request.operation_id,
        saved_at=datetime(2026, 1, 1, tzinfo=UTC),
        request=facts.request.request,
        preview=facts.preview,
    )
    active = SavedMixedElectronicRefundActivation(
        operation_id=saved.operation_id,
        parent_sale_id=facts.parent,
        actor_id=facts.actor,
        branch_id=facts.branch,
        register_id=uuid4(),
        shift_id=uuid4(),
        activated_at=datetime(2026, 1, 2, tzinfo=UTC),
        review=saved,
    )
    bound = MixedElectronicRefundBindingRead(
        state="bound",
        operation_id=saved.operation_id,
        parent_sale_id=facts.parent,
        review_revision_hash=saved.preview.revision_hash,
        bound_at=saved.saved_at,
        cancelled_at=None,
    )
    reconciled = ReconciledMixedElectronicRefund(
        state="requires_reconciliation",
        activation=active,
        confirmations=(),
        missing_payment_ids=tuple(p.original_payment_id for p in facts.preview.payments),
    )
    result = posted()
    result = result.model_copy(
        update={
            "operation_id": saved.operation_id,
            "parent_sale_id": facts.parent,
            "receipt": result.receipt.model_copy(update={"parent_sale_id": facts.parent}),
        }
    )
    calls: dict[str, AsyncMock] = {}
    for service, methods in (
        (review.MixedElectronicRefundPreviewService, {"quote": facts.preview}),
        (review.MixedElectronicRefundIntentService, {"save": saved, "read": saved}),
        (
            binding.MixedElectronicRefundBindingService,
            {"bind": bound, "cancel": bound, "read": bound},
        ),
        (activation.MixedElectronicRefundActivationService, {"start": active, "read": active}),
        (outcome.MixedElectronicRefundOutcomeService, {"record": reconciled, "read": reconciled}),
        (posting.MixedElectronicRefundPostingService, {"post": result, "read": result}),
    ):
        for method, response in methods.items():
            mock = AsyncMock(return_value=response)
            monkeypatch.setattr(service, method, mock)
            calls[f"{service.__name__}.{method}"] = mock
    app = FastAPI()
    for module in (review, binding, activation, outcome, posting):
        app.include_router(module.router, prefix="/api/v1")

    @app.exception_handler(AurumError)
    async def error_response(_request: Request, error: AurumError) -> JSONResponse:
        return JSONResponse({"code": error.code}, status_code=error.http_status)

    actor = CurrentUser(
        user_id=facts.actor,
        tenant_id=facts.tenant,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.refund", "pos.refund_external_confirm"},
        permission_scopes={
            "pos.refund": frozenset({facts.branch}),
            "pos.refund_external_confirm": None,
        },
        password_verified_at=datetime.now(UTC),
    )
    session = MagicMock(spec=AsyncSession)
    session.get = AsyncMock(return_value=type("TenantState", (), {"status": "active"})())

    async def user() -> CurrentUser:
        return actor

    async def db() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, session)

    app.dependency_overrides[current_user] = user
    app.dependency_overrides[get_db] = db
    base = f"/api/v1/sales/{facts.parent}/mixed-electronic-refunds"
    operation = f"{base}/reviews/{saved.operation_id}"
    cases = (
        Case(
            "POST",
            f"{base}/quote",
            facts.request.request.selection.model_dump(mode="json"),
            "MixedElectronicRefundPreviewService.quote",
        ),
        Case(
            "POST",
            f"{base}/reviews",
            facts.request.model_dump(mode="json"),
            "MixedElectronicRefundIntentService.save",
        ),
        Case("GET", operation, None, "MixedElectronicRefundIntentService.read"),
        Case("POST", f"{operation}/bind", None, "MixedElectronicRefundBindingService.bind"),
        Case(
            "POST",
            f"{operation}/cancel-binding",
            None,
            "MixedElectronicRefundBindingService.cancel",
        ),
        Case("GET", f"{operation}/binding", None, "MixedElectronicRefundBindingService.read"),
        Case(
            "POST",
            f"{operation}/activate",
            {"shift_id": str(active.shift_id)},
            "MixedElectronicRefundActivationService.start",
        ),
        Case("GET", f"{operation}/activation", None, "MixedElectronicRefundActivationService.read"),
        Case(
            "POST",
            f"{operation}/confirmations",
            {
                "original_payment_id": str(facts.preview.payments[0].original_payment_id),
                "terminal_id": "synthetic",
                "document_number": "synthetic-document",
            },
            "MixedElectronicRefundOutcomeService.record",
        ),
        Case("GET", f"{operation}/confirmations", None, "MixedElectronicRefundOutcomeService.read"),
        Case(
            "POST",
            f"{operation}/post",
            {"shift_id": str(active.shift_id)},
            "MixedElectronicRefundPostingService.post",
        ),
        Case("GET", f"{operation}/posting", None, "MixedElectronicRefundPostingService.read"),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield Api(
            client, actor, session, facts.parent, saved.operation_id, facts.branch, calls, cases
        )


async def test_every_adapter_preserves_scope_and_only_calls_its_service(mixed_api: Api) -> None:
    api = mixed_api
    for case in api.cases:
        for call in api.calls.values():
            call.reset_mock()
        response = await api.client.request(case.method, case.path, json=case.body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        selected = api.calls[case.call]
        selected.assert_awaited_once()
        arguments = selected.await_args.kwargs
        assert arguments["tenant_id"] == api.actor.tenant_id
        assert arguments["actor_id"] == api.actor.user_id
        assert arguments["allowed_branch_ids"] == {api.branch}
        if not case.call.endswith(".save"):
            assert arguments["parent_sale_id"] == api.parent
        if case.call not in {
            "MixedElectronicRefundPreviewService.quote",
            "MixedElectronicRefundIntentService.save",
        }:
            assert arguments["operation_id"] == api.operation
        assert sum(call.await_count for call in api.calls.values()) == 1


@pytest.mark.parametrize("denial", ["missing_permission", "empty_scope", "missing_tenant"])
async def test_all_routes_fail_closed_before_services(mixed_api: Api, denial: str) -> None:
    api = mixed_api
    if denial == "missing_permission":
        api.actor.permissions.remove("pos.refund")
    elif denial == "empty_scope":
        api.actor.permission_scopes["pos.refund"] = frozenset()
    else:
        api.actor.tenant_id = None
    for case in api.cases:
        response = await api.client.request(case.method, case.path, json=case.body)
        assert response.status_code == (422 if denial == "missing_tenant" else 403)
    assert not any(call.await_count for call in api.calls.values())


async def test_readonly_keeps_quote_and_get_recovery_without_commands(mixed_api: Api) -> None:
    api = mixed_api
    api.session.get.return_value.status = "readonly"
    for case in api.cases:
        response = await api.client.request(case.method, case.path, json=case.body)
        readonly = case.method == "GET" or case.path.endswith("/quote")
        assert response.status_code == (200 if readonly else 422), response.text
        assert api.calls[case.call].await_count == int(readonly)
    api.session.flush.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("proof", ["missing", "stale", "future"])
async def test_external_evidence_requires_recent_proof_but_read_does_not(
    mixed_api: Api, proof: str
) -> None:
    api = mixed_api
    api.actor.password_verified_at = (
        None
        if proof == "missing"
        else datetime.now(UTC) + timedelta(days=-2 if proof == "stale" else 2)
    )
    command = next(case for case in api.cases if case.call.endswith("OutcomeService.record"))
    assert (await api.client.post(command.path, json=command.body)).status_code == 403
    assert (await api.client.get(command.path)).status_code == 200
    api.calls[command.call].assert_not_awaited()


@pytest.mark.parametrize(
    "scope", ["both_wide", "refund_wide", "external_wide", "common", "disjoint"]
)
async def test_external_confirmation_intersects_permission_branches(
    mixed_api: Api, scope: str
) -> None:
    api = mixed_api
    other = uuid4()
    api.actor.permission_scopes = {
        "pos.refund": None if scope in {"both_wide", "refund_wide"} else frozenset({api.branch}),
        "pos.refund_external_confirm": (
            None
            if scope in {"both_wide", "external_wide"}
            else frozenset({other if scope == "disjoint" else api.branch})
        ),
    }
    command = next(case for case in api.cases if case.call.endswith("OutcomeService.record"))
    response = await api.client.post(command.path, json=command.body)
    assert response.status_code == 200, response.text
    expected = None if scope == "both_wide" else set() if scope == "disjoint" else {api.branch}
    assert api.calls[command.call].await_args.kwargs["allowed_branch_ids"] == expected


async def test_external_confirmation_permission_is_additional_not_needed_for_read(
    mixed_api: Api,
) -> None:
    api = mixed_api
    api.actor.permissions.remove("pos.refund_external_confirm")
    command = next(case for case in api.cases if case.call.endswith("OutcomeService.record"))
    assert (await api.client.post(command.path, json=command.body)).status_code == 403
    assert (await api.client.get(command.path)).status_code == 200
    api.calls[command.call].assert_not_awaited()


async def test_payloads_reject_client_owned_money_and_parent_mismatch(mixed_api: Api) -> None:
    api = mixed_api
    for case in api.cases:
        if case.body is None:
            continue
        response = await api.client.post(case.path, json={**case.body, "amount": "0.01"})
        assert response.status_code == 422, response.text
    save = next(case for case in api.cases if case.call.endswith("IntentService.save"))
    mismatch = save.path.replace(str(api.parent), str(uuid4()))
    assert (await api.client.post(mismatch, json=save.body)).status_code == 422
    assert not any(call.await_count for call in api.calls.values())


@pytest.mark.parametrize(
    "invalid_id",
    ["not-a-uuid", "00000000-0000-1000-8000-000000000001"],
    ids=["malformed", "version-1"],
)
async def test_recovery_operation_requires_uuid4(mixed_api: Api, invalid_id: str) -> None:
    api = mixed_api
    for case in api.cases:
        if str(api.operation) not in case.path:
            continue
        path = case.path.replace(str(api.operation), invalid_id)
        assert (await api.client.request(case.method, path, json=case.body)).status_code == 422
    assert not any(call.await_count for call in api.calls.values())


@pytest.mark.parametrize("guard", ["activation", "other"])
async def test_cancel_maps_only_its_known_activation_constraint(mixed_api: Api, guard: str) -> None:
    api = mixed_api
    name = (
        "mixed_electronic_refund_activation_cancel_denied"
        if guard == "activation"
        else "unrelated_check"
    )
    original = type("DatabaseCheck", (Exception,), {"sqlstate": "23514"})(name)
    error = IntegrityError("synthetic", {}, original)
    call = api.calls["MixedElectronicRefundBindingService.cancel"]
    call.side_effect = error
    case = next(case for case in api.cases if case.call.endswith("BindingService.cancel"))
    if guard == "activation":
        assert (await api.client.post(case.path)).status_code == 409
    else:
        with pytest.raises(IntegrityError):
            await api.client.post(case.path)
