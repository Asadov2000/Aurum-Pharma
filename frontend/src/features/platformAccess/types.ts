export type PlatformAccessKind = "developer" | "administrator";
export type PlatformAccessStatus = "pending" | "active" | "revoked" | "expired";
export type PlatformAccessReasonCode =
  | "platform_staff_onboarding"
  | "responsibility_change"
  | "security_incident"
  | "access_review"
  | "other";

export interface PlatformAccessGrant {
  id: string;
  user_id: string;
  user_email: string | null;
  user_full_name: string | null;
  access_kind: PlatformAccessKind;
  capabilities: string[];
  status: PlatformAccessStatus;
  requested_by: string | null;
  request_reason_code: string;
  request_reason: string;
  requested_at: string;
  requires_approval: boolean;
  approval_expires_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  approval_reason_code: string | null;
  approval_reason: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  revoke_reason_code: string | null;
  revoke_reason: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface PlatformAccessGrantList {
  items: PlatformAccessGrant[];
}

export interface PlatformAccessGrantFilters {
  status?: PlatformAccessStatus;
  user_id?: string;
  limit?: number;
}

export interface PlatformAccessActionPayload {
  version: number;
  reason_code: PlatformAccessReasonCode;
  reason: string;
}
