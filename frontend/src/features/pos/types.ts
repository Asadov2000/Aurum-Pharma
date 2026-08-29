import { type CatalogItem } from "@/features/catalog/types";
import { type PosPaymentMethod } from "@/features/foundation/paymentSettings";

// Mirrors backend Pydantic schemas in app/domains/pos/schemas.py.

export type ShiftStatus = "open" | "closed";
export type SaleStatus = "draft" | "completed" | "voided";
export type SaleType = "sale" | "return";
export type PaymentMethod = PosPaymentMethod;
export type LegacyPaymentMethod = "bank_transfer";
export type PaymentMethodRead = PaymentMethod | LegacyPaymentMethod;
export type PaymentAttemptStatus =
  | "pending"
  | "requires_reconciliation"
  | "confirmed"
  | "consumed"
  | "voided";

export interface PaymentMetadata {
  cash_received?: string;
  // Legacy recovery only. New card/QR checkout uses payment_attempt_id.
  external_confirmed?: true;
}

export interface PaymentAttempt {
  id: string;
  tenant_id: string;
  sale_id: string;
  cashier_user_id: string;
  operation_id: string;
  payment_method: "card" | "qr";
  amount: string;
  currency: string;
  status: PaymentAttemptStatus;
  external_reference: string | null;
  void_reason: string | null;
  void_note: string | null;
  created_at: string;
  confirmed_at: string | null;
  consumed_at: string | null;
  voided_at: string | null;
}

export interface PaymentAttemptCreatePayload {
  operation_id: string;
  sale_id: string;
  payment_method: "card" | "qr";
  amount: string;
}

export interface PaymentAttemptConfirmPayload {
  external_reference?: string | null;
}

export interface PaymentAttemptVoidPayload {
  reason: "cashier_cancelled" | "checkout_failed";
  operator_note?: string | null;
}

export interface Shift {
  id: string;
  tenant_id: string;
  branch_id: string;
  register_id: string;
  opened_by_user_id: string;
  closed_by_user_id: string | null;
  opened_at: string;
  closed_at: string | null;
  status: ShiftStatus;
  opening_cash: string;
  closing_cash_actual: string | null;
  closing_cash_expected: string | null;
  closing_difference: string | null;
  totals: Record<string, unknown> | null;
  currency: string;
  notes: string | null;
}

export interface ShiftOpenPayload {
  register_id: string;
  opening_cash?: string;
}

export interface ShiftClosePayload {
  closing_cash_actual: string;
  notes?: string | null;
}

export interface ZReport {
  shift_id: string;
  opened_at: string;
  closed_at: string | null;
  register_id: string;
  cashier_user_id: string;
  opening_cash: string;
  closing_cash_actual: string | null;
  closing_cash_expected: string | null;
  closing_difference: string | null;
  totals: Record<string, unknown>;
  sales_count: number;
  returns_count: number;
}

export interface SaleItem {
  id: string;
  sale_id: string;
  catalog_id: string;
  batch_id: string;
  qty: string;
  unit_price: string;
  total_price: string;
  currency: string;
  discount_amount: string;
  position: number;
  // Additive read-only enrichment from the line's FEFO-chosen batch.
  batch_number?: string | null;
  expires_at?: string | null;
  days_to_expiry?: number | null;
  refunded_qty?: string;
}

export interface Payment {
  id: string;
  sale_id: string;
  operation_id: string | null;
  payment_method: PaymentMethodRead;
  amount: string;
  currency: string;
  payment_attempt_id?: string | null;
}

export interface Sale {
  id: string;
  tenant_id: string;
  branch_id: string;
  register_id: string;
  shift_id: string;
  sale_type: SaleType;
  parent_sale_id: string | null;
  refund_attempt_id?: string | null;
  status: SaleStatus;
  receipt_number: string | null;
  operation_id?: string | null;
  is_test: boolean;
  total_amount: string;
  currency: string;
  voided_at: string | null;
  voided_by_sale_id: string | null;
  cashier_user_id: string;
  created_at: string;
  completed_at: string | null;
}

export interface SaleDetails extends Sale {
  items: SaleItem[];
  payments: Payment[];
}

export interface SaleItemAddedResponse {
  items: SaleItem[];
  requires_prescription_log: boolean;
}

export interface SaleItemDeletedResponse {
  command_type: "item.delete";
  sale_id: string;
  item_id: string;
  status: "deleted";
}

export type PosCommandSavedResult =
  | { command_type: "sale.create"; sale: Sale }
  | { command_type: "item.add"; item_add: SaleItemAddedResponse }
  | { command_type: "item.update"; item: SaleItem }
  | SaleItemDeletedResponse;

export interface PosCommandResult {
  operation_id: string;
  sale_id: string | null;
  created_at: string;
  result: PosCommandSavedResult;
}

export interface PaymentAddPayload {
  operation_id: string;
  // Legacy is accepted only when retrying an operation stored by an older client.
  payment_method: PaymentMethodRead;
  amount: string;
  metadata?: PaymentMetadata | null;
}

export interface PrescriptionLogPayload {
  sale_item_id?: string | null;
  prescription_number?: string | null;
  doctor_name?: string | null;
  doctor_license?: string | null;
  patient_name?: string | null;
  notes?: string | null;
}

export interface PrescriptionLog extends PrescriptionLogPayload {
  id: string;
  sale_id: string;
  created_at: string;
}

export interface SaleCheckoutItemPayload {
  catalog_id: string;
  qty: string;
}

export interface SaleCheckoutPaymentPayload {
  payment_method: PaymentMethod;
  amount: string;
  payment_attempt_id?: string | null;
  metadata?: PaymentMetadata | null;
}

export type SaleCheckoutPrescriptionPayload = Omit<PrescriptionLogPayload, "sale_item_id">;

export interface SaleCheckoutPayload {
  operation_id: string;
  register_id: string;
  draft_sale_id?: string | null;
  items: SaleCheckoutItemPayload[];
  payments: SaleCheckoutPaymentPayload[];
  prescription?: SaleCheckoutPrescriptionPayload | null;
  expired_sale_confirmed?: boolean;
}

export interface SaleCheckoutItemResult {
  id: string;
  catalog_id: string;
  batch_id: string;
  qty: string;
  unit_price: string;
  total_price: string;
  currency: string;
  discount_amount: string;
  position: number;
}

export interface SaleCheckoutPaymentResult {
  id: string;
  payment_method: PaymentMethodRead;
  amount: string;
  currency: string;
}

export interface SaleCheckoutResult {
  event_id: string;
  sale_id: string;
  operation_id: string;
  tenant_id: string;
  branch_id: string;
  register_id: string;
  shift_id: string;
  cashier_user_id: string;
  receipt_number: string;
  receipt_seq: number;
  created_at: string;
  completed_at: string;
  total_amount: string;
  currency: string;
  is_test: boolean;
  items: SaleCheckoutItemResult[];
  payments: SaleCheckoutPaymentResult[];
}

// ---- cashier favorites ----

export interface PosFavorite {
  id: string;
  catalog_id: string;
  created_at: string;
  catalog: CatalogItem;
}

export type PosFavoriteRecord = Omit<PosFavorite, "catalog">;

// ---- receipt (print / PDF) ----

export interface ReceiptLine {
  position: number;
  name: string;
  qty: string;
  unit_price: string;
  discount_amount: string;
  total_price: string;
}

export interface ReceiptPayment {
  method: PaymentMethodRead;
  amount: string;
}

export interface ReceiptData {
  sale_id: string;
  is_refund: boolean;
  status: SaleStatus;
  pharmacy_name: string;
  branch_name: string;
  branch_address: string | null;
  branch_license: string | null;
  receipt_number: string | null;
  datetime: string | null;
  cashier_name: string | null;
  items: ReceiptLine[];
  discount_total: string;
  total: string;
  currency: string;
  payments: ReceiptPayment[];
  paid_total: string;
  change: string;
}

export type ReceiptWidth = "58" | "80" | "A4";
