// Mirrors backend Pydantic schemas in app/domains/roles/schemas.py.

export interface Permission {
  code: string;
  group_code: string;
  name: string;
  description: string | null;
  min_level_required: number;
  is_dangerous: boolean;
  is_active: boolean;
}

export interface Role {
  id: string;
  tenant_id: string | null;
  name: string;
  description: string | null;
  level: number;
  is_system: boolean;
  is_active: boolean;
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
  level: number;
  permissions: string[];
}

export interface RoleUpdatePayload {
  name?: string;
  description?: string | null;
  level?: number;
  permissions?: string[];
}

export interface Assignment {
  id: string;
  user_id: string;
  tenant_id: string;
  branch_id: string | null;
  role_id: string;
  password_required: boolean;
  is_active: boolean;
}

export type UserStatus = "invited" | "active" | "blocked" | "archived";

export interface UserWithAssignments {
  id: string;
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

export interface InviteUserPayload {
  email: string;
  full_name: string;
  role_id: string;
  branch_id?: string | null;
  password_required?: boolean;
}

export interface AssignmentCreatePayload {
  role_id: string;
  branch_id?: string | null;
  password_required?: boolean;
}

export interface UserUpdatePayload {
  full_name?: string;
  phone?: string | null;
}
