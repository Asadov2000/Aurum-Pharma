# Adversarial review: POS, payments, receipts and refunds

Date: 2026-08-29  
Scope: seller and pharmacy-owner flows from sale creation through payment,
receipt, refund and shift close.  
Method: read-only code and test review against the current `main`; the shared
development database was not changed.

## Executive summary

The core accounting transaction is already strong: checkout is atomic,
operation identifiers are tenant-scoped, completed sales are immutable, refund
quantities are bounded, and lost responses can be reconciled without blind
money retries. No critical RLS bypass or direct mutation of completed sales was
found.

Release readiness is nevertheless blocked by four business-safety gaps. The
first implementation package should close AP-POS-001 through AP-POS-004 before
new POS features are added. AP-POS-005 and AP-POS-006 should follow in the
receipt and privacy package. AP-POS-007 is a separate Edge milestone, not a
small web-POS patch.

## Findings

### AP-POS-001 - Confirmed electronic payment can be voided without a refund

Severity: High  
Affected roles: seller, pharmacy owner

Evidence:

- `backend/app/domains/pos/router.py:304` exposes payment-attempt voiding with
  the ordinary `pos.sell` permission.
- `backend/app/domains/pos/service.py:457-469` rejects only a `consumed`
  attempt; `confirmed` and `requires_reconciliation` are changed to `voided`.
- `frontend/src/features/pos/SaleArea.tsx:1198-1217` uses this operation when a
  cashier resets a staged electronic payment.

Impact: a terminal may have charged the customer while Aurum discards the
confirmed attempt. The sale, stock movement and compensating refund then do not
represent the real money movement.

Required fix:

- never void a `confirmed` attempt; resolve it only by checkout or a linked
  compensating refund/reversal;
- allow a `requires_reconciliation` attempt to be closed only by a separately
  authorized manager after terminal evidence is recorded;
- make the UI distinguish "payment did not happen" from "payment happened and
  must be reversed".

Required tests: cashier denial, manager reconciliation, concurrent void versus
checkout, and recovery after the response is lost.

### AP-POS-002 - Manual card/QR confirmation has no mandatory unique evidence

Severity: High  
Affected roles: seller, pharmacy owner

Evidence:

- `backend/app/domains/pos/schemas.py:68-81` permits an empty
  `external_reference`.
- `backend/app/domains/pos/models.py:556` stores no terminal identifier and has
  no uniqueness constraint for terminal evidence.
- `frontend/src/features/pos/SaleArea.tsx:1165-1184` confirms the attempt
  without asking for or sending a terminal document number.

Impact: one real terminal payment can be used to justify multiple Aurum sales,
or a failed payment can be marked successful. The owner cannot reliably match
the shift to the terminal statement.

Required fix:

- require `terminal_id` and `external_reference` for manual confirmation;
- enforce tenant-scoped uniqueness of `(terminal_id, external_reference)` at
  the database level;
- record the confirming user and expose safe reconciliation details to an
  authorized owner/manager;
- replace manual confirmation with a signed provider result when bank
  integration is introduced.

Required tests: empty evidence, duplicate and concurrent duplicate evidence,
cross-tenant reuse, permission checks and receipt/reconciliation read model.

### AP-POS-003 - Active electronic attempts do not block cart changes and shift close

Severity: High  
Affected roles: seller, pharmacy owner

Evidence:

- `backend/app/domains/pos/service.py:2815-2841` updates a draft line without
  checking active payment attempts; the delete path has the same boundary.
- `backend/app/domains/pos/repository.py:268-281` considers only draft items,
  recorded payments and prescriptions when deciding whether a shift can close.
- `backend/app/domains/pos/service.py:1104-1111` separately blocks refund
  attempts, but not payment attempts.

Impact: after an unknown terminal result, the cart can be emptied and the shift
can be closed while a possible customer charge remains unresolved.

Required fix:

- block all amount-changing cart commands while a payment attempt is
  `pending`, `requires_reconciliation` or `confirmed`;
- block shift close while any active payment attempt belongs to its register;
- show the owner a reconciliation queue instead of hiding the unresolved item
  in an old draft.

Required tests: update/delete after an active attempt, closing a shift with an
empty draft but active attempt, and the checkout-versus-close race.

### AP-POS-004 - Customer returns immediately increase sellable stock

Severity: High  
Affected roles: seller, pharmacy owner, inventory controller

Evidence:

- `backend/app/domains/pos/service.py:3927-3937` writes a positive
  `sale_return` movement directly to the original batch.

Impact: medicine that left the pharmacy and may have been stored incorrectly or
tampered with can become available for FEFO sale again. Financial refund and
physical acceptance are different business decisions and must not be coupled.

Required fix:

- complete the financial refund without increasing sellable stock;
- create a quarantined return record with condition, package integrity, reason,
  actor and disposition;
- require a separately authorized decision: return to sellable stock, supplier
  return, write-off or destruction;
- default to quarantine and fail closed.

Required tests: financial refund leaves sellable stock unchanged, quarantine is
tenant/branch isolated, only an authorized disposition changes stock, and no
returned unit is selected by FEFO before release.

### AP-POS-005 - Confirmed payment can conflict with a later settings change

Severity: Medium  
Affected roles: seller, pharmacy owner

Evidence:

- checkout validates current payment settings after the payment attempt was
  created (`backend/app/domains/pos/service.py:1775` and the checkout path near
  `backend/app/domains/pos/service.py:1884`).

Impact: an owner can disable card or QR after the terminal succeeds but before
checkout, leaving a real charge without a completed sale.

Required fix: bind the attempt to the accepted settings revision, or allow an
already confirmed attempt to finish under its immutable creation snapshot while
preventing new attempts with the disabled method.

### AP-POS-006 - Refund evidence and printed return receipt need safer semantics

Severity: Medium  
Affected roles: seller, pharmacy owner, customer

Evidence:

- free-text refund reason and comment are stored in payment metadata
  (`backend/app/domains/pos/service.py:4107-4119`), so staff can accidentally
  put personal data into immutable history;
- the current return print flow does not consistently identify the original
  receipt and can use sale-oriented wording in
  `frontend/src/features/pos/ReceiptPrintModal.tsx` and
  `backend/app/domains/pos/receipt_pdf.py`.

Required fix: use controlled reason codes, redact free text from audit
snapshots, and make browser/PDF return receipts explicitly say "Возврат",
"Возвращено" and show the original receipt number. Do not show sale change or
"Спасибо за покупку" on a return.

### AP-POS-007 - Offline cash core is not yet an executable branch runtime

Severity: Release blocker for the promised 24-hour offline operation  
Affected roles: seller, pharmacy owner

Evidence: `backend/app/domains/pos/edge_cash.py:38` explicitly contains a
domain component without a production route/runtime composition. The current
web POS correctly fails closed when the cloud is unavailable.

Required fix: package a local Branch Edge runtime with a local database,
cash-only offline policy, signed grants, durable queue, restart recovery,
printer support, controlled updates and deterministic synchronization.

Required test: disconnect cloud for 24 hours, sell for cash, print, restart the
PC, retry an operation, reconnect, and prove exactly one sale, one stock effect
and one audit trail after synchronization.

## Controls already verified

- atomic checkout for sale, payments, stock movements and outbox event;
- stable tenant-scoped operation IDs and lost-response recovery;
- database-level immutability of completed sales;
- bounded partial/full refunds linked to the original sale;
- tenant/RLS isolation for sales, payment attempts and refund attempts;
- exact mixed-payment allocation and rejection of unsupported methods;
- consumed payment attempts cannot be reused or voided;
- electronic refund attempts require terminal references and cannot reuse a
  terminal document for another sale.

## Implementation order

1. Electronic payment state machine: AP-POS-001, AP-POS-002 and AP-POS-003.
2. Pharmaceutical return quarantine: AP-POS-004.
3. Settings-race and receipt/privacy corrections: AP-POS-005 and AP-POS-006.
4. Full POS regression matrix and adversarial concurrency tests.
5. Separate Branch Edge executable milestone: AP-POS-007.

Each package must pass targeted tests first, then full backend pytest, full
Vitest and the sale/refund/shift Playwright flows in a disposable environment.
