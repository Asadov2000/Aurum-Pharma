"""Read-only integrity validation for showcase datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.core.db import SupportSessionLocal


class ShowcaseValidationError(RuntimeError):
    """Base error for validator setup or result failures."""


class SupportSessionRequired(ShowcaseValidationError):
    """Raised when validation is not running in the trusted support context."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Aggregate result of one validation check."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """PII-free aggregate validation report."""

    results: tuple[ValidationResult, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {result.name: result.count for result in self.results}

    @property
    def total_violations(self) -> int:
        return sum(result.count for result in self.results)

    @property
    def is_valid(self) -> bool:
        return self.total_violations == 0


@dataclass(frozen=True, slots=True)
class _ValidationCheck:
    name: str
    statement: TextClause


_TRANSACTION_READ_ONLY = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
_ENABLE_SUPPORT_CONTEXT = text("SELECT set_config('app.support_session', 'true', true)")
_VERIFY_CONTEXT = text("""
    /* showcase:validator_context */
    SELECT (
      session_user = 'aurum_support'
      AND public.is_support_session()
      AND current_setting('transaction_read_only') = 'on'
    )
    """)
_VERIFY_PENDING_CONTEXT = text("""
    /* showcase:pending_validator_context */
    SELECT (
      session_user = 'aurum_support'
      AND public.is_support_session()
      AND current_database() = 'aurum_demo'
      AND current_setting('transaction_read_only') = 'off'
    )
    """)

_CHECKS = (
    _ValidationCheck(
        name="negative_batch_balances",
        statement=text("""
            /* showcase:negative_batch_balances */
            SELECT COUNT(*)::bigint
            FROM public.batch
            WHERE qty_remaining < 0
            """),
    ),
    _ValidationCheck(
        name="batch_movement_balance_mismatches",
        statement=text("""
            /* showcase:batch_movement_balance_mismatches */
            WITH movement_totals AS (
              SELECT batch_id, SUM(qty_delta) AS movement_total
              FROM public.batch_movement
              GROUP BY batch_id
            )
            SELECT COUNT(*)::bigint
            FROM public.batch AS batch
            LEFT JOIN movement_totals
              ON movement_totals.batch_id = batch.id
            WHERE batch.qty_remaining IS DISTINCT FROM
                  COALESCE(movement_totals.movement_total, 0::numeric)
            """),
    ),
    _ValidationCheck(
        name="sale_total_mismatches",
        statement=text("""
            /* showcase:sale_total_mismatches */
            WITH item_totals AS (
              SELECT sale_id, SUM(total_price) AS item_total
              FROM public.sale_item
              GROUP BY sale_id
            )
            SELECT COUNT(*)::bigint
            FROM public.sale AS sale
            LEFT JOIN item_totals
              ON item_totals.sale_id = sale.id
            WHERE sale.total_amount IS DISTINCT FROM
                  COALESCE(item_totals.item_total, 0::numeric)
            """),
    ),
    _ValidationCheck(
        name="completed_payment_total_mismatches",
        statement=text("""
            /* showcase:completed_payment_total_mismatches */
            WITH payment_totals AS (
              SELECT sale_id, SUM(amount) AS payment_total
              FROM public.sale_payment
              GROUP BY sale_id
            )
            SELECT COUNT(*)::bigint
            FROM public.sale AS sale
            LEFT JOIN payment_totals
              ON payment_totals.sale_id = sale.id
            WHERE sale.status = 'completed'
              AND sale.total_amount IS DISTINCT FROM
                  COALESCE(payment_totals.payment_total, 0::numeric)
            """),
    ),
    _ValidationCheck(
        name="excessive_return_quantities",
        statement=text("""
            /* showcase:excessive_return_quantities */
            WITH returned_quantities AS (
              SELECT
                return_item.parent_sale_item_id,
                SUM(return_item.qty) AS returned_qty
              FROM public.sale_item AS return_item
              JOIN public.sale AS return_sale
                ON return_sale.id = return_item.sale_id
              WHERE return_sale.sale_type = 'return'
                AND return_sale.status = 'completed'
                AND return_item.parent_sale_item_id IS NOT NULL
              GROUP BY return_item.parent_sale_item_id
            )
            SELECT COUNT(*)::bigint
            FROM returned_quantities
            LEFT JOIN public.sale_item AS original_item
              ON original_item.id = returned_quantities.parent_sale_item_id
            WHERE original_item.id IS NULL
               OR returned_quantities.returned_qty > original_item.qty
            """),
    ),
    _ValidationCheck(
        name="duplicate_receipt_numbers",
        statement=text("""
            /* showcase:duplicate_receipt_numbers */
            SELECT COUNT(*)::bigint
            FROM (
              SELECT tenant_id, receipt_number
              FROM public.sale
              WHERE receipt_number IS NOT NULL
              GROUP BY tenant_id, receipt_number
              HAVING COUNT(*) > 1
            ) AS duplicate_receipts
            """),
    ),
    _ValidationCheck(
        name="accepted_incoming_items_without_batch",
        statement=text("""
            /* showcase:accepted_incoming_items_without_batch */
            SELECT COUNT(*)::bigint
            FROM public.incoming_item AS item
            JOIN public.incoming_document AS document
              ON document.id = item.document_id
            WHERE document.status = 'accepted'
              AND item.created_batch_id IS NULL
            """),
    ),
    _ValidationCheck(
        name="tenant_scope_mismatches",
        statement=text("""
            /* showcase:tenant_scope_mismatches */
            WITH violations AS (
              SELECT 1
              FROM public.register AS register
              LEFT JOIN public.branch AS branch ON branch.id = register.branch_id
              WHERE branch.id IS NULL
                 OR register.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.barcode AS barcode
              LEFT JOIN public.tenant_catalog AS catalog ON catalog.id = barcode.catalog_id
              WHERE catalog.id IS NULL
                 OR barcode.tenant_id IS DISTINCT FROM catalog.tenant_id

              UNION ALL
              SELECT 1
              FROM public.batch AS batch
              LEFT JOIN public.branch AS branch ON branch.id = batch.branch_id
              WHERE branch.id IS NULL
                 OR batch.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.batch AS batch
              LEFT JOIN public.tenant_catalog AS catalog ON catalog.id = batch.catalog_id
              WHERE catalog.id IS NULL
                 OR batch.tenant_id IS DISTINCT FROM catalog.tenant_id

              UNION ALL
              SELECT 1
              FROM public.batch_movement AS movement
              LEFT JOIN public.batch AS batch ON batch.id = movement.batch_id
              WHERE batch.id IS NULL
                 OR movement.tenant_id IS DISTINCT FROM batch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.write_off AS write_off
              LEFT JOIN public.branch AS branch ON branch.id = write_off.branch_id
              WHERE branch.id IS NULL
                 OR write_off.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.write_off AS write_off
              LEFT JOIN public.batch AS batch ON batch.id = write_off.batch_id
              WHERE batch.id IS NULL
                 OR write_off.tenant_id IS DISTINCT FROM batch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.supplier_return AS supplier_return
              LEFT JOIN public.supplier AS supplier
                ON supplier.id = supplier_return.supplier_id
              WHERE supplier.id IS NULL
                 OR supplier_return.tenant_id IS DISTINCT FROM supplier.tenant_id

              UNION ALL
              SELECT 1
              FROM public.supplier_return AS supplier_return
              LEFT JOIN public.batch AS batch ON batch.id = supplier_return.batch_id
              WHERE batch.id IS NULL
                 OR supplier_return.tenant_id IS DISTINCT FROM batch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.supplier_return AS supplier_return
              LEFT JOIN public.incoming_document AS document
                ON document.id = supplier_return.source_document_id
              WHERE supplier_return.source_document_id IS NOT NULL
                AND (
                  document.id IS NULL
                  OR supplier_return.tenant_id IS DISTINCT FROM document.tenant_id
                )

              UNION ALL
              SELECT 1
              FROM public.incoming_document AS document
              LEFT JOIN public.branch AS branch ON branch.id = document.branch_id
              WHERE branch.id IS NULL
                 OR document.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.incoming_document AS document
              LEFT JOIN public.supplier AS supplier ON supplier.id = document.supplier_id
              WHERE supplier.id IS NULL
                 OR document.tenant_id IS DISTINCT FROM supplier.tenant_id

              UNION ALL
              SELECT 1
              FROM public.incoming_item AS item
              LEFT JOIN public.incoming_document AS document
                ON document.id = item.document_id
              WHERE document.id IS NULL
                 OR item.tenant_id IS DISTINCT FROM document.tenant_id

              UNION ALL
              SELECT 1
              FROM public.incoming_item AS item
              LEFT JOIN public.tenant_catalog AS catalog ON catalog.id = item.catalog_id
              WHERE catalog.id IS NULL
                 OR item.tenant_id IS DISTINCT FROM catalog.tenant_id

              UNION ALL
              SELECT 1
              FROM public.incoming_item AS item
              LEFT JOIN public.batch AS batch ON batch.id = item.created_batch_id
              WHERE item.created_batch_id IS NOT NULL
                AND (
                  batch.id IS NULL
                  OR item.tenant_id IS DISTINCT FROM batch.tenant_id
                )

              UNION ALL
              SELECT 1
              FROM public.shift AS shift
              LEFT JOIN public.branch AS branch ON branch.id = shift.branch_id
              WHERE branch.id IS NULL
                 OR shift.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.shift AS shift
              LEFT JOIN public.register AS register ON register.id = shift.register_id
              WHERE register.id IS NULL
                 OR shift.tenant_id IS DISTINCT FROM register.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              LEFT JOIN public.branch AS branch ON branch.id = sale.branch_id
              WHERE branch.id IS NULL
                 OR sale.tenant_id IS DISTINCT FROM branch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              LEFT JOIN public.register AS register ON register.id = sale.register_id
              WHERE register.id IS NULL
                 OR sale.tenant_id IS DISTINCT FROM register.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              LEFT JOIN public.shift AS shift ON shift.id = sale.shift_id
              WHERE shift.id IS NULL
                 OR sale.tenant_id IS DISTINCT FROM shift.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              LEFT JOIN public.sale AS parent_sale ON parent_sale.id = sale.parent_sale_id
              WHERE sale.parent_sale_id IS NOT NULL
                AND (
                  parent_sale.id IS NULL
                  OR sale.tenant_id IS DISTINCT FROM parent_sale.tenant_id
                )

              UNION ALL
              SELECT 1
              FROM public.sale_item AS item
              LEFT JOIN public.sale AS sale ON sale.id = item.sale_id
              WHERE sale.id IS NULL
                 OR item.tenant_id IS DISTINCT FROM sale.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale_item AS item
              LEFT JOIN public.tenant_catalog AS catalog ON catalog.id = item.catalog_id
              WHERE catalog.id IS NULL
                 OR item.tenant_id IS DISTINCT FROM catalog.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale_item AS item
              LEFT JOIN public.batch AS batch ON batch.id = item.batch_id
              WHERE batch.id IS NULL
                 OR item.tenant_id IS DISTINCT FROM batch.tenant_id

              UNION ALL
              SELECT 1
              FROM public.sale_item AS item
              LEFT JOIN public.sale_item AS parent_item
                ON parent_item.id = item.parent_sale_item_id
              WHERE item.parent_sale_item_id IS NOT NULL
                AND (
                  parent_item.id IS NULL
                  OR item.tenant_id IS DISTINCT FROM parent_item.tenant_id
                )

              UNION ALL
              SELECT 1
              FROM public.sale_payment AS payment
              LEFT JOIN public.sale AS sale ON sale.id = payment.sale_id
              WHERE sale.id IS NULL
                 OR payment.tenant_id IS DISTINCT FROM sale.tenant_id

              UNION ALL
              SELECT 1
              FROM public.prescription_log AS prescription
              LEFT JOIN public.sale AS sale ON sale.id = prescription.sale_id
              WHERE sale.id IS NULL
                 OR prescription.tenant_id IS DISTINCT FROM sale.tenant_id

              UNION ALL
              SELECT 1
              FROM public.prescription_log AS prescription
              LEFT JOIN public.sale_item AS item ON item.id = prescription.sale_item_id
              WHERE prescription.sale_item_id IS NOT NULL
                AND (
                  item.id IS NULL
                  OR prescription.tenant_id IS DISTINCT FROM item.tenant_id
                )
            )
            SELECT COUNT(*)::bigint
            FROM violations
            """),
    ),
    _ValidationCheck(
        name="branch_scope_mismatches",
        statement=text("""
            /* showcase:branch_scope_mismatches */
            WITH violations AS (
              SELECT 1
              FROM public.shift AS shift
              JOIN public.register AS register ON register.id = shift.register_id
              WHERE shift.branch_id IS DISTINCT FROM register.branch_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              JOIN public.register AS register ON register.id = sale.register_id
              WHERE sale.branch_id IS DISTINCT FROM register.branch_id

              UNION ALL
              SELECT 1
              FROM public.sale AS sale
              JOIN public.shift AS shift ON shift.id = sale.shift_id
              WHERE sale.branch_id IS DISTINCT FROM shift.branch_id
                 OR sale.register_id IS DISTINCT FROM shift.register_id

              UNION ALL
              SELECT 1
              FROM public.sale AS return_sale
              JOIN public.sale AS original_sale
                ON original_sale.id = return_sale.parent_sale_id
              WHERE return_sale.branch_id IS DISTINCT FROM original_sale.branch_id

              UNION ALL
              SELECT 1
              FROM public.sale_item AS item
              JOIN public.sale AS sale ON sale.id = item.sale_id
              JOIN public.batch AS batch ON batch.id = item.batch_id
              WHERE sale.branch_id IS DISTINCT FROM batch.branch_id

              UNION ALL
              SELECT 1
              FROM public.sale_item AS return_item
              JOIN public.sale AS return_sale ON return_sale.id = return_item.sale_id
              JOIN public.sale_item AS original_item
                ON original_item.id = return_item.parent_sale_item_id
              WHERE return_sale.parent_sale_id IS DISTINCT FROM original_item.sale_id

              UNION ALL
              SELECT 1
              FROM public.incoming_item AS item
              JOIN public.incoming_document AS document
                ON document.id = item.document_id
              JOIN public.batch AS batch ON batch.id = item.created_batch_id
              WHERE document.branch_id IS DISTINCT FROM batch.branch_id

              UNION ALL
              SELECT 1
              FROM public.write_off AS write_off
              JOIN public.batch AS batch ON batch.id = write_off.batch_id
              WHERE write_off.branch_id IS DISTINCT FROM batch.branch_id

              UNION ALL
              SELECT 1
              FROM public.supplier_return AS supplier_return
              JOIN public.incoming_document AS document
                ON document.id = supplier_return.source_document_id
              JOIN public.batch AS batch ON batch.id = supplier_return.batch_id
              WHERE document.branch_id IS DISTINCT FROM batch.branch_id
            )
            SELECT COUNT(*)::bigint
            FROM violations
            """),
    ),
)

CHECK_NAMES: tuple[str, ...] = tuple(check.name for check in _CHECKS)


async def _collect_results(session: AsyncSession) -> ValidationReport:
    results: list[ValidationResult] = []
    for check in _CHECKS:
        query_result = await session.execute(check.statement)
        count = int(query_result.scalar_one())
        if count < 0:
            raise ShowcaseValidationError(
                f"Validation check {check.name!r} returned a negative count."
            )
        results.append(ValidationResult(name=check.name, count=count))

    return ValidationReport(results=tuple(results))


async def validate_showcase(session: AsyncSession) -> ValidationReport:
    """Validate all tenants from a consistent, explicitly read-only snapshot."""

    await session.execute(_TRANSACTION_READ_ONLY)
    await session.execute(_ENABLE_SUPPORT_CONTEXT)
    context_result = await session.execute(_VERIFY_CONTEXT)
    if context_result.scalar_one() is not True:
        raise SupportSessionRequired(
            "Showcase validation requires an aurum_support read-only transaction."
        )
    return await _collect_results(session)


async def validate_pending_showcase(session: AsyncSession) -> ValidationReport:
    """Validate uncommitted seed rows only inside the isolated demo database."""

    context_result = await session.execute(_VERIFY_PENDING_CONTEXT)
    if context_result.scalar_one() is not True:
        raise SupportSessionRequired(
            "Pending showcase validation requires a writable aurum_support "
            "transaction on aurum_demo."
        )
    return await _collect_results(session)


async def validate_database() -> ValidationReport:
    """Open the configured support session and validate without writing."""

    async with SupportSessionLocal() as session:
        async with session.begin():
            return await validate_showcase(session)


def render_report(report: ValidationReport) -> str:
    """Render a deterministic machine-readable report containing counts only."""

    payload: dict[str, object] = {
        "status": "passed" if report.is_valid else "failed",
        "total_violations": report.total_violations,
        "checks": report.counts,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
