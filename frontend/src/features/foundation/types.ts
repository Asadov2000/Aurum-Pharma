// Mirrors backend Pydantic schemas in app/domains/foundation/schemas.py.
// Codegen lands in Phase 2 — until then we keep these by hand.

export type TenantStatus =
  | "setup"
  | "trial"
  | "active"
  | "grace_period"
  | "readonly"
  | "archived";

export interface Tenant {
  id: string;
  name: string;
  legal_name: string | null;
  inn_or_tin: string | null;
  registration_number: string | null;
  contact_email: string;
  contact_phone: string | null;
  legal_address: string | null;
  logo_url: string | null;
  status: TenantStatus;
  setup_started_at: string;
  trial_started_at: string | null;
  trial_ends_at: string | null;
  drug_catalog_mode: string;
  created_at: string;
  updated_at: string;
}

export interface TenantCreatePayload {
  name: string;
  legal_name?: string | null;
  inn_or_tin?: string | null;
  registration_number?: string | null;
  contact_email: string;
  contact_phone?: string | null;
  legal_address?: string | null;
}

export interface OwnerCreatePayload {
  email: string;
  full_name: string;
}

export interface OwnerProvision {
  user_id: string;
  email: string;
  home_tenant_id: string;
  role_id: string;
}

export interface TenantUpdatePayload {
  name?: string;
  legal_name?: string | null;
  inn_or_tin?: string | null;
  registration_number?: string | null;
  contact_email?: string;
  contact_phone?: string | null;
  legal_address?: string | null;
  logo_url?: string | null;
  status?: TenantStatus;
}

export interface ExpiryThresholds {
  yellow: number;
  orange: number;
  red: number;
}

export type ExpiredSaleMode = "strict" | "warning" | "off";
export type RefundReasonMode = "required" | "required_with_text" | "optional" | "off";

export interface TenantSettings {
  tenant_id: string;
  expiry_thresholds: ExpiryThresholds;
  expired_sale_mode: ExpiredSaleMode;
  refund_reason_mode: RefundReasonMode;
  session_admin_minutes: number;
  session_pos_minutes: number;
  pin_mode_enabled: boolean;
  draft_sale_lifetime_min: number;
  prescription_warning_text: string;
  updated_at: string;
}

export interface TenantSettingsUpdatePayload {
  expiry_thresholds?: ExpiryThresholds;
  expired_sale_mode?: ExpiredSaleMode;
  refund_reason_mode?: RefundReasonMode;
  session_admin_minutes?: number;
  session_pos_minutes?: number;
  pin_mode_enabled?: boolean;
  draft_sale_lifetime_min?: number;
  prescription_warning_text?: string;
}

export type BranchType = "pharmacy" | "pharmacy_post" | "kiosk";

export interface Branch {
  id: string;
  tenant_id: string;
  name: string;
  address: string | null;
  branch_type: BranchType;
  license_number: string | null;
  license_expires_at: string | null;
  working_hours: Record<string, unknown> | null;
  receipt_header: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface BranchCreatePayload {
  name: string;
  address?: string | null;
  branch_type?: BranchType;
  license_number?: string | null;
  license_expires_at?: string | null;
}

export interface BranchUpdatePayload {
  name?: string;
  address?: string | null;
  branch_type?: BranchType;
  license_number?: string | null;
  license_expires_at?: string | null;
  is_active?: boolean;
}

export type PrinterType = "browser" | "thermal_58" | "thermal_80" | "a4";

export interface Register {
  id: string;
  tenant_id: string;
  branch_id: string;
  name: string;
  printer_type: PrinterType | null;
  printer_config: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RegisterCreatePayload {
  branch_id: string;
  name: string;
  printer_type?: PrinterType | null;
}

export interface RegisterUpdatePayload {
  name?: string;
  printer_type?: PrinterType | null;
  is_active?: boolean;
}
