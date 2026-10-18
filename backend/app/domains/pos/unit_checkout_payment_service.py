"""Record and recover cash components inside the existing checkout coordinator."""

from app.core.errors import AurumError
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository

Checkout = PhysicalCheckoutResultV3 | MixedCheckoutResultV4


class UnitCheckoutPaymentService:
    def __init__(self, repository: UnitCheckoutPaymentRepository) -> None:
        self.repository = repository

    async def record_cash(self, result: Checkout) -> None:
        if (
            len(result.payments) != 1
            or result.payments[0].method != "cash"
            or result.payments[0].amount != result.total_amount
        ):
            raise AurumError("Cash checkout payment components are inconsistent")
        mixed = isinstance(result, MixedCheckoutResultV4)
        payment = result.payments[0]
        await self.repository.insert(
            UnitCheckoutPaymentComponent(
                tenant_id=result.tenant_id,
                id=payment.id,
                physical_checkout_id=None if mixed else result.operation_id,
                mixed_checkout_id=result.operation_id if mixed else None,
                sale_id=result.sale_id,
                method=payment.method,
                amount=payment.amount,
                currency="TJS",
                payment_attempt_id=None,
                created_by=result.cashier_id,
            )
        )

    async def validate_saved(self, version: int, result: Checkout) -> None:
        if version == 1:
            # Historical checkouts predate the journal; recovery never backfills it.
            return
        if version != 2:
            raise AurumError("Stored checkout payment journal version is unsupported")
        mixed = isinstance(result, MixedCheckoutResultV4)
        components = await self.repository.for_checkout(
            result.tenant_id, result.operation_id, mixed=mixed
        )
        expected = {payment.id: payment for payment in result.payments}
        if len(components) != len(expected):
            raise AurumError("Stored checkout payment journal is inconsistent")
        for component in components:
            payment = expected.get(component.id)
            if (
                payment is None
                or component.tenant_id != result.tenant_id
                or component.sale_id != result.sale_id
                or component.physical_checkout_id != (None if mixed else result.operation_id)
                or component.mixed_checkout_id != (result.operation_id if mixed else None)
                or component.method != payment.method
                or component.amount != payment.amount
                or component.currency != "TJS"
                or component.payment_attempt_id is not None
                or component.created_by != result.cashier_id
            ):
                raise AurumError("Stored checkout payment journal is inconsistent")
