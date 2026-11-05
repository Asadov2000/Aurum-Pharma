import { type MeResponse } from "./types";

export type PermissionUser = Pick<MeResponse, "is_developer"> &
  Partial<Pick<MeResponse, "permissions" | "permission_scopes" | "support_access">>;

function isUnscopedDeveloper(user: PermissionUser | null | undefined): boolean {
  return (
    user?.is_developer === true &&
    (user.support_access === null || user.support_access === undefined)
  );
}

/** Mirrors the server's ordinary developer bypass for client-side UX only. */
export function hasPermission(user: PermissionUser | null | undefined, code: string): boolean {
  return isUnscopedDeveloper(user) || user?.permissions?.includes(code) === true;
}

/** Mirrors the server's branch authorization for UI visibility only. */
export function hasPermissionForBranch(
  user: PermissionUser | null | undefined,
  code: string,
  branchId: string | null | undefined,
): boolean {
  if (!branchId || !hasPermission(user, code)) return false;
  if (isUnscopedDeveloper(user)) return true;
  const scope = user?.permission_scopes?.[code];
  return scope === null || (Array.isArray(scope) && scope.includes(branchId));
}

export function hasAnyPermission(
  user: PermissionUser | null | undefined,
  codes: readonly string[],
): boolean {
  return codes.some((code) => hasPermission(user, code));
}
