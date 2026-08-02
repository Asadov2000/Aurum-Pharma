// Mirrors backend Pydantic schemas in app/domains/inventory/schemas.py.

export type ExpiryStatus = "expired" | "red" | "orange" | "yellow" | "normal";
export type WriteOffReason = "expired" | "damaged" | "spoiled" | "theft" | "other";

// movement_type comes from the DB free-text — these are the values the
// backend writes today. Keep as string to stay forward-compatible.
export type MovementType = string;

export interface Batch {
  id: string;
  tenant_id: string;
  branch_id: string;
  catalog_id: string;
  batch_number: string | null;
  manufactured_at: string | null;
  expires_at: string;
  purchase_price: string; // Decimal as string
  sale_price: string;
  currency: string;
  qty_initial: string;
  qty_remaining: string;
  is_blocked: boolean;
  block_reason: string | null;
  blocked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface BatchWithExpiry extends Batch {
  branch_name: string;
  catalog_name: string;
  catalog_form: string | null;
  catalog_dosage: string | null;
  catalog_pack_size: string | null;
  expiry_status: ExpiryStatus;
  days_to_expiry: number;
}

export interface BatchSummary {
  total_qty: string;
  purchase_value: string;
  sale_value: string;
  attention_count: number;
  expired_count: number;
  blocked_count: number;
}

export interface BatchList {
  items: BatchWithExpiry[];
  total: number;
  page: number;
  page_size: number;
  summary: BatchSummary;
}

export interface Movement {
  id: string;
  batch_id: string;
  movement_type: MovementType;
  qty_delta: string;
  source_table: string | null;
  source_id: string | null;
  notes: string | null;
  created_at: string;
}

export interface BatchDetails extends BatchWithExpiry {
  report_timezone: string;
  recent_movements: Movement[];
}

export interface WriteOffCreatePayload {
  operation_id: string;
  qty: string;
  reason: WriteOffReason;
  comment?: string | null;
}

export interface WriteOff {
  id: string;
  batch_id: string;
  qty: string;
  reason: WriteOffReason;
  comment: string | null;
  amount: string;
  currency: string;
  created_at: string;
}

export interface BatchSearchParams {
  catalog_id?: string;
  branch_id?: string;
  expiry_status?: ExpiryStatus;
  batch_number?: string;
  is_blocked?: boolean;
  show_empty?: boolean;
  page?: number;
  page_size?: number;
}
