// Mirrors backend Pydantic schemas in app/domains/roles/schemas.py.

export interface Permission {
  code: string;
  group_code: string;
  name: string;
  description: string | null;
  is_dangerous: boolean;
  is_active: boolean;
  scope_type: "PLATFORM" | "TENANT_ALL" | "BRANCH_SET" | "OWN";
  target_role_type: "platform" | "tenant";
  risk_level: "normal" | "sensitive" | "critical";
  requires_step_up: boolean;
  requires_confirmation: boolean;
}

export interface Role {
  id: string;
  tenant_id: string | null;
  name: string;
  description: string | null;
  is_system: boolean;
  is_protected: boolean;
  protected_kind: "developer" | "administrator" | "tenant_owner" | null;
  is_active: boolean;
  version: number;
  permissions: string[];
}

export interface RoleTemplate {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_system: boolean;
  is_active: boolean;
  permissions: string[];
}

export interface RoleCreatePayload {
  name: string;
  description?: string | null;
  permissions: string[];
}

export interface RoleUpdatePayload {
  name?: string;
  description?: string | null;
  permissions?: string[];
}

export interface Assignment {
  id: string;
  user_id: string;
  tenant_id: string;
  membership_id: string;
  branch_id: string | null;
  role_id: string;
  password_required: boolean;
  is_active: boolean;
}

export type UserStatus = "pending" | "active" | "suspended" | "offboarded";

export interface UserWithAssignments {
  id: string;
  membership_id: string;
  email: string;
  full_name: string;
  phone: string | null;
  status: UserStatus;
  last_login_at: string | null;
  assignments: Assignment[];
}

export interface UserListResponse {
  items: UserWithAssignments[];
  total: number;
  page: number;
  page_size: number;
}

export interface AssignmentCreatePayload {
  role_id: string;
  branch_id?: string | null;
  password_required?: boolean;
}

export interface UserUpdatePayload {
  full_name?: string;
  phone?: string | null;
  status?: "active";
}
