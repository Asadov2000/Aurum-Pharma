export interface ShiftHistoryItem {
  id: string;
  branch_id: string;
  branch_name: string;
  register_id: string;
  register_name: string;
  cashier_user_id: string;
  cashier_name: string | null;
  opened_at: string;
  closed_at: string | null;
  status: "open" | "closed" | "suspended";
  opening_cash: string;
  closing_cash_actual: string | null;
  closing_cash_expected: string | null;
  closing_difference: string | null;
  sales_total: string;
  returns_total: string;
  sales_count: number;
  returns_count: number;
  currency: string;
}

export interface ShiftHistoryList {
  items: ShiftHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ShiftHistoryParams {
  status?: "open" | "closed" | "suspended";
  branch_id?: string;
  register_id?: string;
  cashier_query?: string;
  date_from?: string;
  date_to?: string;
  page: number;
  page_size: number;
}

export interface ReportPaymentBreakdown {
  cash: string;
  card: string;
  qr: string;
  bank_transfer: string;
  mixed: string;
}

export interface SalesSummaryDay {
  day: string;
  gross_sales: string;
  total_discounts: string;
  total_refunds: string;
  net: string;
  sales_count: number;
  returns_count: number;
}

export interface SalesSummaryOverview {
  date_from: string;
  date_to: string;
  branch_name: string | null;
  currency: string;
  gross_sales: string;
  total_discounts: string;
  total_refunds: string;
  net: string;
  sales_count: number;
  returns_count: number;
  average_sale: string;
  payment_breakdown: ReportPaymentBreakdown;
  daily: SalesSummaryDay[];
}

export interface SalesSummaryParams {
  from: string;
  to: string;
  branch_id?: string;
}
