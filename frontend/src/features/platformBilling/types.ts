export type PlatformInvoiceStatus = "pending" | "overdue" | "paid" | "cancelled";

export interface PlatformBillingOverview {
  generated_at: string;
  tenants_total: number;
  active_subscriptions: number;
  attention_subscriptions: number;
  open_invoices: number;
  overdue_invoices: number;
  outstanding_amount: string;
  currency: string;
}

export interface PlatformBillingTenant {
  tenant_id: string;
  name: string;
  tenant_status: string;
  subscription_status: string | null;
}

export interface PlatformBillingTenantList {
  items: PlatformBillingTenant[];
  total: number;
  page: number;
  page_size: number;
}

export interface PlatformBillingTenantFilters {
  q?: string;
  page: number;
  page_size: number;
}

export interface PlatformInvoice {
  tenant_name: string;
  invoice_number: string;
  issued_at: string;
  due_at: string;
  amount: string;
  paid_amount: string;
  outstanding_amount: string;
  currency: string;
  status: PlatformInvoiceStatus;
  subscription_status: string;
}

export interface PlatformInvoiceList {
  items: PlatformInvoice[];
  total: number;
  page: number;
  page_size: number;
}

export interface PlatformInvoiceFilters {
  q?: string;
  status?: PlatformInvoiceStatus;
  page: number;
  page_size: number;
}

export type PlatformPricingStatus = "draft" | "scheduled" | "active" | "archived" | "cancelled";

export type PlatformPricingAudience = "default" | "new_customers";

export interface PlatformPricingVersion {
  price_version_id: string;
  plan_id: string | null;
  version_number: number;
  status: PlatformPricingStatus;
  monthly_price_per_branch: string;
  annual_discount_pct: string;
  currency: "TJS";
  audience: PlatformPricingAudience;
  effective_from: string | null;
  notice_days: number;
  change_reason: string | null;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  activated_at: string | null;
  archived_at: string | null;
  row_version: number;
  created_at: string;
}

export interface PlatformPricingPlan {
  plan_id: string;
  code: string;
  name: string;
  description: string | null;
  currency: "TJS";
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  versions: PlatformPricingVersion[];
}

export interface PlatformPricingPlanList {
  items: PlatformPricingPlan[];
  total: number;
  page: number;
  page_size: number;
}

export interface PricingPlanCreate {
  operation_id: string;
  code: string;
  name: string;
  description?: string | null;
}

export interface PricingPriceDraftCreate {
  operation_id: string;
  monthly_price_per_branch: string;
  annual_discount_pct: string;
  audience: PlatformPricingAudience;
  notice_days: number;
  change_reason: string;
}

export interface PricingSchedule {
  operation_id: string;
  expected_row_version: number;
  effective_from: string;
}

export interface PricingActivate {
  operation_id: string;
  expected_row_version: number;
}

export type PricingCancellationReason =
  | "pricing_error"
  | "commercial_change"
  | "legal_requirement"
  | "security_incident"
  | "other";

export interface PricingCancel extends PricingActivate {
  reason_code: PricingCancellationReason;
  reason: string;
}

export interface PlatformPricingPlanCommandResult {
  item: PlatformPricingPlan;
  applied: boolean;
}

export interface PlatformPricingVersionCommandResult {
  item: PlatformPricingVersion;
  applied: boolean;
}

export type PlatformFinancialInvoiceDocumentState = "issued" | "void";
export type PlatformFinancialInvoiceSettlementState =
  | "unpaid"
  | "partially_paid"
  | "paid"
  | "written_off";
export type PlatformFinancialInvoiceCollectionState = "not_due" | "due" | "overdue";

export interface PlatformFinancialInvoice {
  invoice_id: string;
  tenant_id: string;
  subscription_id: string;
  price_application_id: string;
  price_application_kind: "initial" | "renewal";
  invoice_number: string;
  document_state: PlatformFinancialInvoiceDocumentState;
  settlement_state: PlatformFinancialInvoiceSettlementState;
  collection_state: PlatformFinancialInvoiceCollectionState;
  period_start: string;
  period_end: string;
  due_at: string;
  total_amount: string;
  outstanding_amount: string;
  currency: "TJS";
  issued_at: string;
}

export interface PlatformBankPaymentReviewCreate {
  operation_id: string;
  target_invoice_id: string;
  amount: string;
  paid_at: string;
  recipient_account_key: string;
  external_reference: string;
}

export type PlatformBankPaymentReviewStatus =
  | "pending_approval"
  | "approved"
  | "rejected"
  | "duplicate";

export interface PlatformBankPaymentReview {
  review_id: string;
  tenant_id: string;
  target_invoice_id: string;
  amount: string;
  currency: "TJS";
  paid_at: string;
  status: PlatformBankPaymentReviewStatus;
  row_version: number;
  created_at: string;
  decided_at?: string | null;
  reason_code?: string | null;
}

export interface PlatformBankPaymentReviewCommandResult {
  item: PlatformBankPaymentReview;
  applied: boolean;
}

export interface PlatformPaymentApprovalQueueItem {
  review_id: string;
  tenant_id: string;
  tenant_name: string;
  target_invoice_id: string;
  invoice_number: string;
  amount: string;
  currency: "TJS";
  paid_at: string;
  status: "pending_approval";
  row_version: number;
  created_at: string;
  is_own_review: boolean;
}

export interface PlatformPaymentApprovalQueue {
  items: PlatformPaymentApprovalQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface PlatformBankPaymentApprove {
  operation_id: string;
  expected_row_version: number;
}

export type PlatformBankPaymentReviewRejectionReason =
  | "bank_payment_not_found"
  | "amount_mismatch"
  | "date_mismatch"
  | "duplicate"
  | "wrong_tenant_or_invoice"
  | "other";

export interface PlatformBankPaymentReviewReject extends PlatformBankPaymentApprove {
  reason_code: PlatformBankPaymentReviewRejectionReason;
  reason_note: string | null;
}

export interface PlatformPaymentAllocation {
  invoice_id: string;
  invoice_number: string;
  amount: string;
  allocation_order: number;
}

export type PlatformPaymentLifecycleState = "confirmed" | "reversed";
export type PlatformSubscriptionStatus =
  | "trial"
  | "active"
  | "grace_period"
  | "suspended"
  | "cancelled"
  | "archived";

export interface PlatformPaymentApproval {
  review_id: string;
  payment_id: string;
  tenant_id: string;
  target_invoice_id: string;
  amount: string;
  currency: "TJS";
  paid_at: string;
  confirmed_at: string;
  lifecycle_state: PlatformPaymentLifecycleState;
  allocated_amount: string;
  credit_amount: string;
  target_outstanding_amount: string;
  blocking_outstanding_amount: string;
  allocations: PlatformPaymentAllocation[];
  access_restored: boolean;
  subscription_status: PlatformSubscriptionStatus;
  subscription_period_start: string;
  subscription_period_end: string;
}

export interface PlatformPaymentApprovalCommandResult {
  item: PlatformPaymentApproval;
  applied: boolean;
}

export interface PlatformPaymentHistoryItem {
  payment_id: string;
  amount: string;
  allocated_amount: string;
  credit_amount: string;
  corrected_amount: string;
  refunded_amount: string;
  reversible_amount: string;
  adjustment_pending: boolean;
  currency: "TJS";
  paid_at: string;
  confirmed_at: string;
  lifecycle_state: PlatformPaymentLifecycleState;
}

export type PlatformPaymentAdjustmentKind = "correction" | "bank_refund";
export type PlatformPaymentAdjustmentReason =
  | "payment_entered_in_error"
  | "amount_correction"
  | "bank_refund_completed"
  | "contract_resolution"
  | "other";

export interface PlatformPaymentAdjustmentCreate {
  operation_id: string;
  adjustment_kind: PlatformPaymentAdjustmentKind;
  amount: string;
  reason_code: PlatformPaymentAdjustmentReason;
  reason_note: string;
  refunded_at: string | null;
  refund_reference: string | null;
}

export interface PlatformPaymentAdjustmentRequest {
  adjustment_id: string;
  tenant_id: string;
  payment_id: string;
  adjustment_kind: PlatformPaymentAdjustmentKind;
  amount: string;
  currency: "TJS";
  reason_code: PlatformPaymentAdjustmentReason;
  reason_note: string;
  refunded_at: string | null;
  status: "pending_approval";
  row_version: number;
  created_at: string;
}

export interface PlatformPaymentAdjustmentRequestCommandResult {
  item: PlatformPaymentAdjustmentRequest;
  applied: boolean;
}

export interface PlatformPaymentAdjustmentQueueItem extends PlatformPaymentAdjustmentRequest {
  tenant_name: string;
  payment_amount: string;
  payment_paid_at: string;
  is_own_request: boolean;
}

export interface PlatformPaymentAdjustmentQueue {
  items: PlatformPaymentAdjustmentQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface PlatformPaymentAdjustmentApprove {
  operation_id: string;
  expected_row_version: number;
}

export type PlatformPaymentAdjustmentRejectionReason =
  | "bank_refund_not_verified"
  | "amount_mismatch"
  | "request_not_supported"
  | "duplicate"
  | "other";

export interface PlatformPaymentAdjustmentReject extends PlatformPaymentAdjustmentApprove {
  reason_code: PlatformPaymentAdjustmentRejectionReason;
  reason_note: string | null;
}

export interface PlatformPaymentAdjustmentApproval {
  adjustment_id: string;
  adjustment_record_id: string;
  tenant_id: string;
  payment_id: string;
  adjustment_kind: PlatformPaymentAdjustmentKind;
  amount: string;
  credit_reversed_amount: string;
  allocation_reversed_amount: string;
  total_adjusted_amount: string;
  reversible_amount: string;
  blocking_outstanding_amount: string;
  access_review_required: boolean;
  currency: "TJS";
  status: "approved";
  approved_at: string;
}

export interface PlatformPaymentAdjustmentApprovalCommandResult {
  item: PlatformPaymentAdjustmentApproval;
  applied: boolean;
}

export interface PlatformPaymentAdjustmentRejectionCommandResult {
  item: {
    adjustment_id: string;
    status: "rejected";
    decision_reason_code: PlatformPaymentAdjustmentRejectionReason;
  };
  applied: boolean;
}

export interface PlatformFinancialAccount {
  tenant_id: string;
  currency: "TJS";
  outstanding_amount: string;
  credit_balance: string;
  invoices: PlatformFinancialInvoice[];
  payments: PlatformPaymentHistoryItem[];
  journal_balanced: boolean;
}
