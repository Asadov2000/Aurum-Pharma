export type TenantAccessModule =
  | "catalog"
  | "incoming"
  | "inventory"
  | "suppliers"
  | "pos"
  | "staff"
  | "settings";

// Sanitized tenant projection: internal administrative reasons are never requested.
export interface TenantAccessPolicy {
  tenant_id: string;
  mode: "full" | "readonly";
  blocked_modules: TenantAccessModule[];
  max_branches: number | null;
  max_users: number | null;
  max_registers: number | null;
  expires_at: string | null;
  version: number;
  updated_at: string;
}
