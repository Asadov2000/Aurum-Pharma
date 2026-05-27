// Mirrors backend app/domains/dashboard/schemas.py.

export interface TodaySection {
  revenue: string; // Decimal as string
  currency: string;
  receipts: number;
  active_shifts: number;
  cashiers_on_shift: number;
}

export type ExpiryStatus = "expired" | "red" | "orange" | "yellow" | "normal";

export interface ExpiringBatch {
  id: string;
  batch_number: string | null;
  branch_id: string;
  expires_at: string;
  days_to_expiry: number;
  expiry_status: ExpiryStatus;
  qty_remaining: string;
}

export interface ExpiringLicense {
  branch_id: string;
  branch_name: string;
  license_expires_at: string;
  days_left: number;
}

export interface ExpiringSection {
  batches: ExpiringBatch[];
  licenses: ExpiringLicense[];
}

export interface FinanceSection {
  subscription_status: string | null;
  subscription_period_end: string | null;
  open_invoices_count: number;
  open_invoices_total: string;
  currency: string;
  has_overdue: boolean;
}

export interface ChecklistSection {
  draft_incoming_count: number;
  closed_shifts_count: number;
  latest_closed_shift_id: string | null;
}

export interface DashboardSummary {
  today: TodaySection;
  expiring: ExpiringSection;
  finance: FinanceSection;
  checklist: ChecklistSection;
  generated_at: string;
}
