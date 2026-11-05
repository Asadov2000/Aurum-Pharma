import { getAuthEpoch } from "@/features/auth/logoutState";
import { hasPermissionForBranch } from "@/features/auth/permissions";
import { getCurrentShift } from "@/features/pos/api";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { useAuthStore } from "@/stores/auth";

interface Scope {
  tenantId: string;
  userId: string;
  branchId: string;
  registerId: string;
  contextCheck?: () => void;
}
/** UX guard only. The server remains authoritative, including recent-account MFA. */
export function mixedElectronicRefundGuard(
  scope: Scope,
  write = false,
  external = false,
): () => void {
  const active = capturePosSessionGuard(scope),
    epoch = getAuthEpoch();
  return () => {
    scope.contextCheck?.();
    const { user, accessToken } = useAuthStore.getState();
    if (
      !active() ||
      epoch !== getAuthEpoch() ||
      !user ||
      !accessToken ||
      !hasPermissionForBranch(user, "pos.refund", scope.branchId) ||
      (write && user.support_access?.is_read_only) ||
      (external && !hasPermissionForBranch(user, "pos.refund_external_confirm", scope.branchId))
    )
      throw new Error("Контекст или доступ к возврату изменился. Откройте операцию заново.");
  };
}
export async function mixedElectronicRefundAuthority(
  scope: Scope,
  check: () => void,
  shiftId?: string,
): Promise<void> {
  check();
  const shift = await getCurrentShift(scope.registerId);
  check();
  if (
    !shift ||
    shift.status !== "open" ||
    shift.opened_by_user_id !== scope.userId ||
    shift.register_id !== scope.registerId ||
    shift.branch_id !== scope.branchId ||
    shift.currency !== "TJS" ||
    (shiftId !== undefined && shift.id !== shiftId)
  )
    throw new Error("Нужна своя открытая смена исходной кассы.");
}
