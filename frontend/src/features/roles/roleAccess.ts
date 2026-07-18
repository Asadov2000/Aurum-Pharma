import { type Role } from "./types";

export const ROLE_EDIT_BLOCKED_MESSAGE =
  "Роль содержит функции, недоступные для изменения. Редактирование заблокировано.";

export function isProtectedRole(role: Role): boolean {
  return role.is_system || role.is_protected === true || role.tenant_id === null;
}

export function isManageableRole(role: Role, tenantId: string | null | undefined): boolean {
  return Boolean(tenantId) && role.tenant_id === tenantId && !isProtectedRole(role);
}

export function hasUnavailableRolePermissions(role: Role): boolean {
  return role.has_hidden_permissions;
}
