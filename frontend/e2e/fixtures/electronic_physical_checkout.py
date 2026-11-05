"""Synthetic original checkout through real services, disposable E2E backend only.

The host verifies immutable Compose IDs before supplying this script over stdin.
No bank call, cash-marker mutation, trigger bypass or production guard override.
"""

import asyncio
import json
import sys
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.db import AppSessionLocal, app_engine
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_quote import UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository
from app.domains.pos.unit_electronic_checkout_service import UnitElectronicCheckoutService
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_binding_service import UnitPaymentBindingService
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from app.domains.pos.unit_payment_intent_service import UnitPaymentIntentService


async def main() -> None:
    database, tenant, actor, branch, sale, mode = sys.argv[1:]
    settings = get_settings()
    url = make_url(settings.DATABASE_URL_APP)
    assert settings.ENVIRONMENT == "development"
    assert url.host == "postgres" and url.database == database
    assert url.username == "aurum_app"
    tenant_id, actor_id, branch_id, sale_id = map(UUID, (tenant, actor, branch, sale))
    assert mode in {"card", "qr", "split"}
    async with AppSessionLocal() as session:
        async with session.begin():
            for key, value in (("tenant_id", tenant), ("user_id", actor)):
                await session.execute(
                    text("SELECT set_config(:key, :value, true)"),
                    {"key": f"app.{key}", "value": value},
                )
            scope = {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "allowed_branch_ids": {branch_id},
            }
            quote = await UnitCartQuoteService(UnitCartRepository(session)).quote(
                **scope, sale_id=sale_id
            )
            assert quote.cashier_id == actor_id and quote.tenant_id == tenant_id
            assert quote.total_amount == Decimal("2.00")
            assert {line.inventory_kind for line in quote.lines} == {"physical_unit"}
            payments = (
                [
                    {"method": "cash", "amount": "0.50"},
                    {"method": "card", "amount": "0.50"},
                    {"method": "qr", "amount": "1.00"},
                ]
                if mode == "split"
                else [{"method": mode, "amount": "2.00"}]
            )
            request = SaveUnitPaymentIntentRequest.model_validate(
                {
                    "operation_id": str(uuid4()),
                    "request": {
                        "sale_id": sale,
                        "expected_revision_hash": quote.revision_hash,
                        "expected_total_amount": "2.00",
                        "payments": payments,
                        "cash_tendered": "0.50" if mode == "split" else None,
                    },
                }
            )
            await UnitPaymentIntentService(UnitPaymentIntentRepository(session)).save(
                **scope, request=request
            )
            identity = {**scope, "sale_id": sale_id, "operation_id": request.operation_id}
            await UnitPaymentBindingService(UnitPaymentBindingRepository(session)).bind(**identity)
            checkout = UnitElectronicCheckoutService(UnitElectronicCheckoutRepository(session))
            attempts = await checkout.prepare_attempts(
                **identity,
                attempt_ids={part.method: uuid4() for part in request.request.payments
                             if part.method != "cash"},
            )
            # Synthetic operator evidence uses the real reconciliation/confirmation service.
            # It neither invokes nor impersonates an external terminal or bank response.
            pos = POSService(POSRepository(session))
            for attempt in attempts:
                await pos.begin_payment_attempt_reconciliation(
                    tenant_id=tenant_id, actor_id=actor_id, attempt_id=attempt.id
                )
                await pos.confirm_payment_attempt(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    attempt_id=attempt.id,
                    terminal_id="E2E-SYNTHETIC",
                    external_reference=f"synthetic-{attempt.id}",
                )
            result = await checkout.complete(**identity)
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            marker = await session.scalar(
                text("SELECT payment_journal_version FROM pos_physical_checkout "
                     "WHERE tenant_id=:tenant AND sale_id=:sale"),
                {"tenant": tenant_id, "sale": sale_id},
            )
            assert marker == 3 and not result.is_test
        print(json.dumps({"sale_id": str(result.sale_id), "shift_id": str(result.shift_id),
                          "receipt_number": result.receipt_number,
                          "total_amount": str(result.total_amount)}))
    await app_engine.dispose()


try:
    asyncio.run(main())
except Exception as error:
    # Do not emit connection URLs, SQL parameters, receipts or raw backend logs.
    raise SystemExit(f"Disposable electronic checkout fixture failed ({type(error).__name__})")
