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
