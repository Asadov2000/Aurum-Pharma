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
  reason?: string | null;
  comment?: string | null;
}
