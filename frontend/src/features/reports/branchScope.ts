import { permissionBranchScope } from "@/features/auth/branchPermissions";
import { useAuth } from "@/features/auth/hooks";
import { useBranchesQuery } from "@/features/foundation/queries";

export function useReportBranchScope() {
  const { user } = useAuth();
  const scope = permissionBranchScope(user, "reports.view");
  const canView = scope === null || scope.length > 0;
  const defaultBranchId = scope === null ? "" : ([...scope].sort()[0] ?? "");
  return {
    scope,
    canView,
    defaultBranchId,
    scopeKey: scope === null ? "all" : [...scope].sort().join(","),
  };
}

export function useReportBranches() {
  const access = useReportBranchScope();
  const branches = useBranchesQuery(false, access.canView);
  return {
    ...branches,
    data: branches.data?.filter(
      (branch) => access.scope === null || access.scope.includes(branch.id),
    ),
    ...access,
  };
}
