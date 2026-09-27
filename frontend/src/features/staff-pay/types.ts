export interface StaffPayPeriod {
  id: string;
  employee_id: string;
  period_from: string;
  period_to: string;
  amount: string;
  paid_amount: string;
  advance_amount: string;
  remaining_amount: string;
  currency: string;
  note: string;
  row_version: number;
  created_at: string;
  updated_at: string;
}

export interface StaffPayEntry {
  id: string;
  kind: "payment" | "advance" | "reversal";
  amount: string;
  paid_on: string;
  note: string;
  is_reversed: boolean;
  reverses_entry_id: string | null;
  created_at: string;
}

export interface StaffPayRevision {
  id: string;
  amount: string;
  note: string;
  row_version: number;
  created_at: string;
}

export interface StaffPayEmployee {
  id: string;
  full_name: string;
  status: string;
}

export interface StaffPayList<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  timezone: string;
}

export interface PeriodCreate {
  employee_id: string;
  period_from: string;
  period_to: string;
  amount: string;
  note: string;
  operation_id: string;
}

export interface PeriodUpdate {
  amount: string;
  note: string;
  expected_row_version: number;
}

export interface EntryCreate {
  kind: "payment" | "advance";
  amount: string;
  paid_on: string;
  note: string;
  operation_id: string;
}
