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
