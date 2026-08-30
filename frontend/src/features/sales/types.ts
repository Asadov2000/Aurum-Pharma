import { type RefundReasonCode } from "@/lib/refundReasons";

// Mirrors backend app/domains/pos/schemas.py SaleListItem / SaleList.

export interface SaleListItem {
  id: string;
  receipt_number: string | null;
  completed_at: string | null;
  branch_name: string | null;
  register_name: string | null;
  cashier_name: string | null;
  total_amount: string;
  currency: string;
  payment_methods: string[];
  is_refund: boolean;
  parent_sale_id: string | null;
  parent_receipt_number: string | null;
  has_refund: boolean;
  refund_receipt_number: string | null;
  items_summary: string;
  status: string;
}

export interface SaleList {
  items: SaleListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SaleSearchParams {
  date_from?: string;
  date_to?: string;
  receipt_number?: string;
  branch_id?: string;
  register_id?: string;
  cashier_id?: string;
  has_refund?: boolean;
  sale_type?: "sale" | "return";
  min_total?: string;
  max_total?: string;
  page?: number;
  page_size?: number;
}

export interface RefundLine {
  sale_item_id: string;
  qty: string;
}

export interface RefundPayload {
  operation_id: string;
  items: RefundLine[];
  reason?: RefundReasonCode | null;
  comment?: string | null;
  refund_attempt_id?: string | null;
}

export type RefundAttemptStatus =
  | "pending"
  | "requires_reconciliation"
  | "confirmed"
  | "consumed"
  | "voided";
export type ElectronicRefundMethod = "card" | "qr" | "bank_transfer";

export interface RefundAttemptPayment {
  payment_method: ElectronicRefundMethod;
  amount: string;
  terminal_id: string | null;
  document_number: string | null;
  confirmed_by_user_id: string | null;
  confirmed_at: string | null;
}

export interface RefundAttempt {
  id: string;
  tenant_id: string;
  parent_sale_id: string;
  register_id: string;
  requested_by_user_id: string;
  confirmed_by_user_id: string | null;
  operation_id: string;
  items: RefundLine[];
  payments: RefundAttemptPayment[];
  total_amount: string;
  external_amount: string;
  currency: "TJS";
  status: RefundAttemptStatus;
  void_reason: string | null;
  void_note: string | null;
  created_at: string;
  confirmed_at: string | null;
  consumed_at: string | null;
  voided_at: string | null;
}

export interface RefundAttemptConfirmation {
  payment_method: ElectronicRefundMethod;
  terminal_id: string;
  document_number: string;
}
