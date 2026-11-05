import { hasPermission, type PermissionUser } from "./permissions";
export { hasPermissionForBranch } from "./permissions";

function isUnscopedDeveloper(user: PermissionUser | null | undefined): boolean {
  return (
    user?.is_developer === true &&
    (user.support_access === null || user.support_access === undefined)
  );
}

/** null means every branch; an array is the exact visible set. */
export function permissionBranchScope(
  user: PermissionUser | null | undefined,
  code: string,
): readonly string[] | null {
  if (!hasPermission(user, code)) return [];
  if (isUnscopedDeveloper(user)) return null;
  const scope = user?.permission_scopes?.[code];
  return scope === null ? null : (scope ?? []);
}
