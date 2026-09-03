export type PaymentReconciliationStatus = "requires_reconciliation" | "confirmed";

export interface PaymentReconciliationItem {
  id: string;
  sale_id: string;
  branch_id: string;
  branch_name: string;
  register_id: string;
  register_name: string;
  configured_terminal_id: string | null;
  cashier_name: string | null;
  payment_method: "card" | "qr";
  amount: string;
  sale_total_amount: string;
  currency: "TJS";
  status: PaymentReconciliationStatus;
  item_count: number;
  created_at: string;
  reconciliation_started_at: string;
  confirmed_at: string | null;
}

export interface PaymentReconciliationList {
  items: PaymentReconciliationItem[];
  total: number;
  page: number;
  page_size: number;
  summary: {
    requires_reconciliation_count: number;
    requires_reconciliation_amount: string;
    confirmed_count: number;
    confirmed_amount: string;
  };
  branches: Array<{ id: string; name: string }>;
}

export interface PaymentReconciliationParams {
  branch_id?: string;
  payment_method?: "card" | "qr";
  status?: PaymentReconciliationStatus;
  page: number;
  page_size: number;
}

export type RefundReconciliationStatus = "pending" | "requires_reconciliation" | "confirmed";

export interface RefundReconciliationItem {
  id: string;
  parent_sale_id: string;
  parent_receipt_number: string;
  branch_id: string;
  branch_name: string;
  register_id: string;
  register_name: string;
  requested_by_name: string | null;
  total_amount: string;
  external_amount: string;
  currency: "TJS";
  status: RefundReconciliationStatus;
  item_count: number;
  payment_methods: Array<"card" | "qr" | "bank_transfer">;
  created_at: string;
  confirmed_at: string | null;
}

export interface RefundReconciliationList {
  items: RefundReconciliationItem[];
  total: number;
  page: number;
  page_size: number;
  summary: {
    pending_count: number;
    pending_external_amount: string;
    requires_reconciliation_count: number;
    requires_reconciliation_external_amount: string;
    confirmed_count: number;
    confirmed_external_amount: string;
  };
  branches: Array<{ id: string; name: string }>;
}

export interface RefundReconciliationParams {
  branch_id?: string;
  status?: RefundReconciliationStatus;
  page: number;
  page_size: number;
}
