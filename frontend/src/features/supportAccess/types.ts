export interface SupportCapability {
  code: string;
  group_code: string;
  name: string;
  description: string | null;
  is_dangerous: boolean;
  risk_level: "normal" | "sensitive" | "critical";
}

export interface SupportAccessSession {
  id: string;
  tenant_id: string;
  tenant_name: string;
  actor_user_id: string;
  reason: string;
  capabilities: string[];
  is_read_only: boolean;
  started_at: string;
  expires_at: string;
  revoked_at: string | null;
}

export interface SupportAccessSessionCreate {
  tenant_id: string;
  reason: string;
  duration_minutes: number;
  capabilities: string[];
}

export interface SupportAccessSessionList {
  items: SupportAccessSession[];
}
