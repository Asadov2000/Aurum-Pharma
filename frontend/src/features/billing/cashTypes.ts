export interface CashPayment {
  id: string;
  tenant_id: string;
  document_number: string;
  status: "pending_approval" | "confirmed" | "rejected";
  row_version: number;
  amount: string;
  currency: "TJS";
  paid_at: string;
  purpose: string;
  target_invoice_id: string | null;
  created_by: string;
  created_at: string;
  confirmed_at: string | null;
  allocated_amount: string;
  credit_amount: string;
  can_approve: boolean;
  can_adjust: false;
}

export interface CashPaymentList {
  items: CashPayment[];
  total: number;
  limit: number;
  offset: number;
}

export interface CashPaymentCreate {
  operation_id: string;
  amount: string;
  paid_at: string;
  purpose: string;
  target_invoice_id: string | null;
}

export interface CashPaymentDecision {
  operation_id: string;
  expected_row_version: number;
}

export interface CashReceipt {
  id: string;
  document_number: string;
  tenant_id: string;
  tenant_name: string;
  purpose: string;
  amount: string;
  currency: "TJS";
  paid_at: string;
  confirmed_at: string;
  allocated_amount: string;
  credit_amount: string;
  allocations: { invoice_id: string; invoice_number: string; amount: string }[];
  title: "Подтверждение приёма наличных";
}
