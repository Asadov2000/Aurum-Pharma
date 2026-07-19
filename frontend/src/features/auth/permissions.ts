import { type MeResponse } from "./types";

type PermissionUser = Pick<MeResponse, "is_developer"> &
  Partial<Pick<MeResponse, "permissions">>;

/** Mirrors the server's ordinary developer bypass for client-side UX only. */
export function hasPermission(
  user: PermissionUser | null | undefined,
  code: string,
): boolean {
  return user?.is_developer === true || user?.permissions?.includes(code) === true;
}

export function hasAnyPermission(
  user: PermissionUser | null | undefined,
  codes: readonly string[],
): boolean {
  return codes.some((code) => hasPermission(user, code));
}
