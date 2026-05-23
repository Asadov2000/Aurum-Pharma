// Mirrors backend Pydantic schemas in app/domains/billing/schemas.py.

export type BillingPeriod = "monthly" | "yearly";
export type PaymentMethod = "bank_transfer" | "card" | "cash";
export type SubscriptionStatus =
  | "trial"
  | "active"
  | "past_due"
  | "cancelled"
  | "expired";
export type InvoiceStatus = "open" | "paid" | "void" | "overdue";

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
