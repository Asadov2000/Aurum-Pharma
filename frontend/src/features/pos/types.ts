// Mirrors backend Pydantic schemas in app/domains/pos/schemas.py.

export type ShiftStatus = "open" | "closed";
export type SaleStatus = "draft" | "completed" | "voided";
export type SaleType = "sale" | "return";
export type PaymentMethod = "cash" | "card" | "bank_transfer";

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
}

export interface Payment {
  id: string;
  sale_id: string;
  operation_id: string | null;
  payment_method: PaymentMethod;
  amount: string;
  currency: string;
}

export interface Sale {
  id: string;
  tenant_id: string;
  branch_id: string;
  register_id: string;
  shift_id: string;
  sale_type: SaleType;
  parent_sale_id: string | null;
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

export interface PaymentAddPayload {
  operation_id: string;
  payment_method: PaymentMethod;
  amount: string;
  metadata?: Record<string, unknown> | null;
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
  metadata?: Record<string, unknown> | null;
}

export type SaleCheckoutPrescriptionPayload = Omit<PrescriptionLogPayload, "sale_item_id">;

export interface SaleCheckoutPayload {
  operation_id: string;
  register_id: string;
  draft_sale_id?: string | null;
  items: SaleCheckoutItemPayload[];
  payments: SaleCheckoutPaymentPayload[];
  prescription?: SaleCheckoutPrescriptionPayload | null;
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
  payment_method: PaymentMethod;
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
  method: PaymentMethod;
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
