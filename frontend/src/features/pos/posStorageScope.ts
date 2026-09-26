import { getAuthSessionGeneration, useAuthStore } from "@/stores/auth";

export interface PosStorageScope {
  tenantId: string;
  userId: string;
}

export function currentPosStorageScope(): PosStorageScope | null {
  const user = useAuthStore.getState().user;
  return user?.active_tenant_id ? { tenantId: user.active_tenant_id, userId: user.id } : null;
}

export function posStorageKey(prefix: string, entityId: string, scope: PosStorageScope): string {
  return `${prefix}${encodeURIComponent(JSON.stringify([scope.tenantId, scope.userId, entityId]))}`;
}

export function capturePosSessionGuard(scope: PosStorageScope): () => boolean {
  const generation = getAuthSessionGeneration();
  return () => {
    const current = currentPosStorageScope();
    return (
      generation === getAuthSessionGeneration() &&
      current?.tenantId === scope.tenantId &&
      current.userId === scope.userId
    );
  };
}
