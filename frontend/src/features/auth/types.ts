export interface TokenPair {
  status?: "authenticated";
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export type MfaChallengeStatus =
  | "mfa_required"
  | "mfa_enrollment_required"
  | "mfa_recovery_required";

export interface MfaChallengeResponse {
  status: MfaChallengeStatus;
  challenge_token: string;
  expires_in: number;
}

export type LoginVerifyResponse = TokenPair | MfaChallengeResponse;

export interface MfaEnrollmentSetup {
  status: "mfa_enrollment_ready";
  secret: string;
  provisioning_uri: string;
  recovery_codes: string[];
  expires_in: number;
}

export interface MeResponse {
  id: string;
  email: string;
  full_name: string;
  is_developer: boolean;
  is_administrator: boolean;
  home_tenant_id: string | null;
  active_tenant_id: string | null;
  status: string;
  last_login_at: string | null;
  level: number;
  is_tenant_owner: boolean;
  branch_assignments: Record<string, string>;
  permissions: string[];
  support_access: {
    id: string;
    tenant_id: string;
    tenant_name: string;
    reason: string;
    capabilities: string[];
    is_read_only: boolean;
    expires_at: string;
  } | null;
}

export interface ActiveSession {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  is_current: boolean;
}

export interface SessionListResponse {
  items: ActiveSession[];
}

export interface SessionRevokeResponse {
  status: "ok";
  revoked_count: number;
}

export interface LoginCodeRequest {
  email: string;
}

export interface LoginCodeResponse {
  status: string;
  // Populated only in dev — the backend leaks the freshly minted code so the
  // UI can pre-fill it without making the user grep Celery logs.
  dev_code: string | null;
}

export interface LoginVerifyRequest {
  email: string;
  code: string;
  password?: string;
}

export interface MfaChallengeRequest {
  challenge_token: string;
}

export interface MfaCodeRequest extends MfaChallengeRequest {
  code: string;
}

export interface MfaRecoveryRequest extends MfaChallengeRequest {
  recovery_code: string;
}
