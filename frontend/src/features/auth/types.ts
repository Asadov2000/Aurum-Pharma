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

export interface LoginVerifyRequest {
  email: string;
  code: string;
  password?: string;
}
