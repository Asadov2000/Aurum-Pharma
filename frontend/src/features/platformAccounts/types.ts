export type PlatformStaffStatus = "invited" | "active" | "blocked" | "offboarded";

export interface PlatformStaffAccount {
  user_id: string;
  email: string;
  full_name: string;
  status: PlatformStaffStatus;
  version: number;
  invited_at: string;
  invitation_expires_at: string | null;
  activated_at: string | null;
  blocked_at: string | null;
  offboarded_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlatformStaffAccountList {
  items: PlatformStaffAccount[];
  total: number;
}

export interface PlatformStaffAccountFilters {
  q?: string;
  status?: PlatformStaffStatus;
  limit?: number;
  offset?: number;
}

export interface PlatformStaffInvitationPayload {
  email: string;
  full_name: string;
}

export interface PlatformStaffInvitation extends PlatformStaffAccount {
  activation_token: string | null;
}

export interface PlatformStaffActivationPayload {
  token: string;
  password: string;
}

export type PlatformAccountAction = "reinvite" | "block" | "unblock" | "offboard";

export type PlatformAccountReasonCode =
  | "invitation_delivery"
  | "responsibility_change"
  | "security_incident"
  | "access_review"
  | "employment_ended"
  | "other";

export interface PlatformAccountLifecyclePayload {
  version: number;
  operation_id: string;
  reason_code: PlatformAccountReasonCode;
  reason: string;
}
