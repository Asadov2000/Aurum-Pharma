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
  has_hidden_permissions: boolean;
  active_assignment_count: number;
}

export interface RoleVersion {
  id: string;
  role_id: string;
  version: number;
  name: string;
  description: string | null;
  status: "draft" | "published" | "archived";
  permissions: string[];
  published_at: string | null;
  archived_at: string | null;
  created_at: string;
  created_by: string | null;
  created_by_name: string | null;
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
  expected_version: number;
  name?: string;
  description?: string | null;
  permissions?: string[];
}

export interface RoleArchivePayload {
  expected_version: number;
  replacement_role_id: string;
}

export interface RoleArchiveResponse {
  archived_version: number;
  affected_memberships: number;
}

export interface Assignment {
  id: string;
  user_id: string;
  tenant_id: string;
  membership_id: string;
  branch_id: string | null;
  role_id: string;
  role_version_id: string;
  role_name: string | null;
  password_required: boolean;
  is_active: boolean;
}

export type UserStatus = "pending" | "active" | "suspended" | "offboarded";

export interface UserWithAssignments {
  id: string;
  membership_id: string;
  is_tenant_owner: boolean;
  email: string;
  full_name: string;
  phone: string | null;
  status: UserStatus;
  last_login_at: string | null;
  can_require_password: boolean;
  assignments: Assignment[];
}

export interface UserListResponse {
  items: UserWithAssignments[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserSearchParams {
  q?: string;
  status?: UserStatus;
  role_id?: string;
  branch_id?: string;
  page?: number;
  page_size?: number;
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

export type OwnershipTransferStatus = "pending" | "completed" | "cancelled" | "expired";

export interface OwnershipTransfer {
  id: string;
  tenant_id: string;
  initiator_membership_id: string;
  initiator_user_id: string;
  initiator_full_name: string;
  target_membership_id: string;
  target_user_id: string;
  target_full_name: string;
  status: OwnershipTransferStatus;
  expires_at: string;
  completed_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OwnershipTransferActionResponse {
  transfer: OwnershipTransfer;
  sessions_revoked: boolean;
}

export interface OwnershipTransferCreatePayload {
  operation_id: string;
  target_membership_id: string;
}
