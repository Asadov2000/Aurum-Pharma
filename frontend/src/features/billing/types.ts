// Mirrors backend Pydantic schemas in app/domains/billing/schemas.py.

export type BillingPeriod = "monthly" | "yearly";
export type PaymentMethod = "bank_transfer" | "card" | "cash";
export type SubscriptionStatus =
  | "trial"
  | "active"
  | "grace_period"
  | "suspended"
  | "cancelled"
  | "archived";
export type InvoiceStatus = "pending" | "paid" | "overdue" | "cancelled";

export interface Plan {
  id: string;
  code: string;
  name: string;
  description: string | null;
  price_per_branch: string;
  currency: string;
  billing_period: BillingPeriod;
  annual_discount_pct: string;
  features: Record<string, unknown> | null;
  is_active: boolean;
}

export interface Subscription {
  id: string;
  tenant_id: string;
  plan_id: string;
  status: SubscriptionStatus;
  billing_period: BillingPeriod;
  period_start: string;
  period_end: string;
  branches_count: number;
  amount: string;
  currency: string;
  cancelled_at: string | null;
}

export interface SubscriptionWithPlan extends Subscription {
  plan_name: string;
  plan_code: string;
  plan_features: Record<string, unknown> | null;
}

export interface SubscriptionCreatePayload {
  plan_id: string;
  billing_period: BillingPeriod;
  branches_count: number;
}

export interface Invoice {
  id: string;
  tenant_id: string;
  subscription_id: string;
  invoice_number: string;
  issued_at: string;
  due_at: string;
  amount: string;
  currency: string;
  discount_amount: string;
  discount_reason: string | null;
  status: InvoiceStatus;
  paid_at: string | null;
  notes: string | null;
}

export interface InvoiceCreatePayload {
  subscription_id: string;
  amount: string;
  due_in_days: number;
  notes?: string | null;
  discount_amount?: string;
  discount_reason?: string | null;
}

export interface Payment {
  id: string;
  invoice_id: string;
  amount: string;
  currency: string;
  method: PaymentMethod;
  reference: string | null;
  paid_at: string;
  notes: string | null;
  created_at: string;
}

export interface PaymentCreatePayload {
  amount: string;
  paid_at: string;
  method: PaymentMethod;
  reference?: string | null;
  notes?: string | null;
}

export interface InvoiceWithPayments extends Invoice {
  payments: Payment[];
}

export type FinancialInvoiceDocumentState = "issued" | "void";
export type FinancialInvoiceSettlementState = "unpaid" | "partially_paid" | "paid" | "written_off";
export type FinancialInvoiceCollectionState = "not_due" | "due" | "overdue";
export type FinancialInvoiceDisplayStatus =
  | "unpaid"
  | "partially_paid"
  | "paid"
  | "written_off"
  | "overdue"
  | "void";

export interface TenantBillingSubscription {
  status: SubscriptionStatus;
  plan_name: string;
  billing_period: BillingPeriod;
  period_start: string;
  period_end: string;
  branches_count: number;
  amount: string;
  currency: "TJS";
}

export interface TenantFinancialInvoice {
  invoice_id: string;
  invoice_number: string;
  document_state: FinancialInvoiceDocumentState;
  settlement_state: FinancialInvoiceSettlementState;
  collection_state: FinancialInvoiceCollectionState;
  period_start: string;
  period_end: string;
  due_at: string;
  total_amount: string;
  outstanding_amount: string;
  currency: "TJS";
  issued_at: string;
}

export interface TenantBillingPayment {
  amount: string;
  allocated_amount: string;
  credit_amount: string;
  corrected_amount: string;
  refunded_amount: string;
  currency: "TJS";
  paid_at: string;
  confirmed_at: string;
  lifecycle_state: "confirmed" | "reversed";
}

export interface TenantFinancialAccount {
  subscription: TenantBillingSubscription | null;
  currency: "TJS";
  outstanding_amount: string;
  credit_balance: string;
  invoices: TenantFinancialInvoice[];
  payments: TenantBillingPayment[];
}

export type TenantPaymentSubmissionStatus =
  | "submitted"
  | "under_review"
  | "approved"
  | "rejected"
  | "duplicate"
  | "withdrawn";

export interface TenantPaymentSubmission {
  submission_id: string;
  tenant_id: string;
  target_invoice_id: string;
  invoice_number: string;
  amount: string;
  currency: "TJS";
  paid_at: string;
  reference_suffix: string;
  status: TenantPaymentSubmissionStatus;
  row_version: number;
  created_at: string;
  decided_at?: string | null;
  reason_code?: string | null;
  can_withdraw?: boolean;
}

export interface TenantPaymentSubmissionList {
  items: TenantPaymentSubmission[];
  total: number;
  page: number;
  page_size: number;
}

export interface TenantPaymentSubmissionCreate {
  operation_id: string;
  target_invoice_id: string;
  amount: string;
  paid_at: string;
  external_reference: string;
}

export interface TenantPaymentSubmissionWithdraw {
  operation_id: string;
  expected_row_version: number;
}

export interface TenantPaymentSubmissionCommandResult {
  item: TenantPaymentSubmission;
  applied: boolean;
}
