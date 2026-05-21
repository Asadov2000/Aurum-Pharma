export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface MeResponse {
  id: string;
  email: string;
  full_name: string;
  is_developer: boolean;
  is_administrator: boolean;
  home_tenant_id: string | null;
  status: string;
  last_login_at: string | null;
  branch_assignments: Record<string, string>;
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
