"""Mixed electronic persistence uses one frozen-draft final sale update."""

from datetime import datetime

from app.domains.pos.mixed_cash_checkout_repository import MixedCashCheckoutRepository
from app.domains.pos.mixed_documents import MixedReceiptDocumentV4
from app.domains.pos.models import Sale
from app.domains.pos.unit_electronic_checkout_repository import UnitElectronicCheckoutRepository


class MixedElectronicCheckoutRepository(MixedCashCheckoutRepository):
    async def complete_sale(
        self, sale: Sale, receipt: MixedReceiptDocumentV4, completed_at: datetime
    ) -> None:
        sale.total_amount = receipt.total_amount
        await super().complete_sale(sale, receipt, completed_at)

    async def validate_completion(self) -> None:
        await UnitElectronicCheckoutRepository(self.session).validate_attempt_bindings()
        await super().validate_completion()
