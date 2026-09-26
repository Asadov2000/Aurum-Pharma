"""Historical imports into disposable databases, separate from runtime identities.

Immutable imported rows intentionally survive each test. The isolated test runner
owns and drops the entire database; no audit/financial trigger is disabled here.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.billing.models import (
    BillingPlan,
    BillingPriceVersion,
    BillingSubscriptionPriceApplication,
    SubscriptionPlan,
    TenantSubscription,
)
from app.domains.foundation.models import Tenant


async def seed_historical_account(
    maintenance_engine: AsyncEngine,
    *,
    invoice_count: int = 1,
    tenant_status: Literal["active", "readonly"] = "readonly",
) -> tuple[UUID, UUID, list[UUID]]:
    """Insert a complete historical invoice and balanced receivable journal."""
    factory = async_sessionmaker(maintenance_engine, expire_on_commit=False)
    suffix = uuid4().hex
    now = utc_now()
    amount = Decimal("590.00")
    async with factory() as session, session.begin():
        assert await session.scalar(text("SELECT session_user")) == "aurum_migrator"
        tenant = Tenant(
            name=f"Historical billing {suffix}",
            contact_email=f"history-{suffix}@test.aurum.tj",
            status=tenant_status,
        )
        session.add(tenant)
        await session.flush()
        actor = AppUser(
            email=f"history-actor-{suffix}@test.aurum.tj",
            full_name="Historical importer",
            home_tenant_id=tenant.id,
            status="active",
        )
        session.add(actor)
        legacy = SubscriptionPlan(
            code=f"history_{suffix}",
            name="Historical plan",
            price_per_branch=amount,
            currency="TJS",
            billing_period="monthly",
            annual_discount_pct=Decimal("0"),
            features={},
        )
        session.add(legacy)
        await session.flush()
        subscription = TenantSubscription(
            tenant_id=tenant.id,
            plan_id=legacy.id,
            status="suspended",
            billing_period="monthly",
            period_start=now - timedelta(days=90),
            period_end=now - timedelta(days=1),
            branches_count=1,
            amount=amount,
            currency="TJS",
        )
        plan = BillingPlan(
            code=f"history_{suffix}",
            name="Historical plan",
            currency="TJS",
            legacy_subscription_plan_id=legacy.id,
            created_by=actor.id,
        )
        session.add_all([subscription, plan])
        await session.flush()
        price = BillingPriceVersion(
            plan_id=plan.id,
            version_number=1,
            status="draft",
            monthly_price_per_branch=amount,
            annual_discount_pct=Decimal("0"),
            currency="TJS",
            audience="default",
            notice_days=30,
            terms_snapshot={},
            created_by=actor.id,
        )
        session.add(price)
        await session.flush()
        invoice_ids: list[UUID] = []
        for index in range(invoice_count):
            period_start = now - timedelta(days=90 - index * 30)
            period_end = period_start + timedelta(days=30)
            actor_session = uuid4()
            application = BillingSubscriptionPriceApplication(
                tenant_id=tenant.id,
                subscription_id=subscription.id,
                plan_id=plan.id,
                application_kind="initial" if index == 0 else "renewal",
                source_type="price_version",
                price_version_id=price.id,
                plan_code=plan.code,
                plan_name=plan.name,
                billing_period="monthly",
                period_start=period_start,
                period_end=period_end,
                calendar_anchor_day=period_start.day,
                timezone="Asia/Dushanbe",
                branches_count=1,
                monthly_price_per_branch=amount,
                annual_discount_pct=Decimal("0"),
                calculated_amount=amount,
                currency="TJS",
                terms_snapshot={},
                operation_id=uuid4(),
                request_hash="0" * 64,
                request_payload={},
                actor_user_id=actor.id,
                actor_session_id=actor_session,
                mfa_verified_at=now,
                result_snapshot={},
                created_at=now,
            )
            session.add(application)
            await session.flush()
            invoice_id, operation_id, entry_id = uuid4(), uuid4(), uuid4()
            params = {
                "tenant": tenant.id,
                "actor": actor.id,
                "session": actor_session,
                "subscription": subscription.id,
                "application": application.id,
                "invoice": invoice_id,
                "operation": operation_id,
                "entry": entry_id,
                "number": f"HIST-{suffix}-{index}",
                "start": period_start,
                "end": period_end,
            }
            await session.execute(
                text("""
                INSERT INTO public.billing_financial_operation
                  (operation_id,operation_type,tenant_id,actor_user_id,actor_session_id,
                   mfa_verified_at,request_hash,request_payload,result_snapshot)
                VALUES (:operation,'invoice_issued',:tenant,:actor,:session,now(),
                        repeat('0',64),'{}','{}')
            """),
                params,
            )
            await session.execute(
                text("""
                INSERT INTO public.billing_invoice
                  (id,tenant_id,subscription_id,price_application_id,operation_id,invoice_number,
                   document_type,document_state,period_start,period_end,due_at,subtotal_amount,
                   total_amount,issuer_snapshot,customer_snapshot,template_version,issued_by,
                   issued_session_id,mfa_verified_at)
                VALUES (:invoice,:tenant,:subscription,:application,:operation,:number,'invoice',
                        'issued',:start,:end,:end,590,590,'{}','{}','historical-v1',:actor,:session,now())
            """),
                params,
            )
            await session.execute(
                text("""
                INSERT INTO public.billing_invoice_line
                  (tenant_id,invoice_id,line_number,line_type,description,quantity,unit_price,
                   period_start,period_end,subtotal_amount,total_amount)
                VALUES (:tenant,:invoice,1,'subscription','Historical invoice',
                        1,590,:start,:end,590,590)
            """),
                params,
            )
            await session.execute(
                text("""
                INSERT INTO public.billing_journal_entry
                  (id,tenant_id,operation_id,entry_sequence,entry_type,actor_user_id,actor_session_id)
                VALUES (:entry,:tenant,:operation,1,'invoice_issued',:actor,:session)
            """),
                params,
            )
            await session.execute(
                text("""
                INSERT INTO public.billing_journal_posting
                  (tenant_id,entry_id,posting_sequence,account_code,side,amount,invoice_id)
                VALUES (:tenant,:entry,1,'accounts_receivable','debit',590,:invoice),
                       (:tenant,:entry,2,'subscription_revenue','credit',590,:invoice)
            """),
                params,
            )
            await session.execute(
                text("SELECT public.assert_billing_invoice_totals(:invoice)"), params
            )
            await session.execute(
                text("SELECT public.assert_billing_journal_entry_balanced(:entry)"), params
            )
            invoice_ids.append(invoice_id)
    return tenant.id, subscription.id, invoice_ids


async def seed_historical_submission(
    maintenance_engine: AsyncEngine,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
    amount: str,
    reference: str,
) -> UUID:
    """Import a pre-retirement submission and its immutable creation event."""
    factory = async_sessionmaker(maintenance_engine, expire_on_commit=False)
    submission_id, operation_id = uuid4(), uuid4()
    async with factory() as session, session.begin():
        assert await session.scalar(text("SELECT session_user")) == "aurum_migrator"
        params = {
            "tenant": tenant_id,
            "invoice": invoice_id,
            "id": submission_id,
            "operation": operation_id,
            "amount": Decimal(amount),
            "reference": reference,
        }
        await session.execute(
            text("""
            INSERT INTO public.billing_payment_submission
              (id,tenant_id,target_invoice_id,amount,paid_at,external_reference,
               create_operation_id,submitted_by,submitted_session_id)
            SELECT :id,:tenant,:invoice,:amount,now()-interval '5 minutes',:reference,
                   :operation,issued_by,issued_session_id FROM public.billing_invoice
            WHERE tenant_id=:tenant AND id=:invoice
        """),
            params,
        )
        await session.execute(
            text("""
            INSERT INTO public.billing_payment_submission_event
              (operation_id,tenant_id,submission_id,event_type,actor_user_id,
               actor_session_id,request_hash,result_snapshot)
            SELECT :operation,:tenant,:id,'payment_submission_created',issued_by,
                   issued_session_id,repeat('0',64),'{}' FROM public.billing_invoice
            WHERE tenant_id=:tenant AND id=:invoice
        """),
            params,
        )
    return submission_id
