// Mirrors backend Pydantic schemas in app/domains/audit/schemas.py.

export interface AuditEntry {
  id: string;
  tenant_id: string | null;
  user_id: string | null;
  action: string;
  table_name: string;
  record_id: string | null;
  old_values: Record<string, unknown> | null;
  new_values: Record<string, unknown> | null;
  changed_fields: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditPage {
  items: AuditEntry[];
  total: number;
  page: number;
  page_size: number;
}

export type AuditScope = "my" | "tenant" | "global";

export interface AuditSearchParams {
  scope: AuditScope;
  action?: string;
  table_name?: string;
  user_id?: string;
  tenant_id?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}
