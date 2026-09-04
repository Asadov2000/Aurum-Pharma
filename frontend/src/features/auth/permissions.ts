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

export function hasAnyPermission(
  user: PermissionUser | null | undefined,
  codes: readonly string[],
): boolean {
  return codes.some((code) => hasPermission(user, code));
}
