import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { useAuth } from "@/features/auth/hooks";
import { hasPermission } from "@/features/auth/permissions";
import { type PosStorageScope } from "@/features/pos/posStorageScope";

import { MixedDispositionHistoryPanel } from "./MixedDispositionHistoryPanel";
import { MixedDispositionModal } from "./MixedDispositionModal";
import { loadMixedDisposition, type PendingMixedDisposition } from "./mixedDispositionRecovery";
import { customerReturnsKeys } from "./queries";
import { type MixedCustomerReturnItem } from "./types";

export interface MixedDispositionActions {
  onResolve: (item: MixedCustomerReturnItem) => void;
  canResolve: (item: MixedCustomerReturnItem) => boolean;
  blocked: boolean;
}
function readPending(scope: PosStorageScope | null): {
  pending: PendingMixedDisposition | null;
  error: string | null;
} {
  try {
    return { pending: scope ? loadMixedDisposition(scope) : null, error: null };
  } catch {
    return {
      pending: null,
      error:
        "Сохранённое решение не удалось прочитать. Новые решения заблокированы; обратитесь к администратору.",
    };
  }
}
export function MixedDispositionWorkspace({
  children,
}: {
  children?: (actions: MixedDispositionActions) => ReactNode;
}): JSX.Element {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [scope] = useState<PosStorageScope | null>(() =>
    user?.id && user.active_tenant_id ? { tenantId: user.active_tenant_id, userId: user.id } : null,
  );
  const [view, setView] = useState<"pending" | "history">("pending");
  const [storage, setStorage] = useState(() => readPending(scope));
  const [dialog, setDialog] = useState<{
    item: MixedCustomerReturnItem | null;
    pending: PendingMixedDisposition | null;
  } | null>(null);
  const usable =
    scope !== null &&
    user?.id === scope.userId &&
    user.active_tenant_id === scope.tenantId &&
    !user.support_access;
  const canResolve = (item: MixedCustomerReturnItem): boolean =>
    usable && hasPermissionForBranch(user, "customer_returns.resolve", item.branch_id);
  const blocked = !usable || storage.pending !== null || storage.error !== null;
  const canView = hasPermission(user, "customer_returns.view");
  useEffect(() => {
    const changed = (): void => setStorage(readPending(scope));
    window.addEventListener("storage", changed);
    window.addEventListener("focus", changed);
    return () => {
      window.removeEventListener("storage", changed);
      window.removeEventListener("focus", changed);
    };
  }, [scope]);
  const onResolve = (item: MixedCustomerReturnItem): void => {
    const latest = readPending(scope);
    setStorage(latest);
    if (!canResolve(item) || latest.pending || latest.error) return;
    setDialog({ item, pending: null });
  };
  const refresh = (): void => {
    setStorage(readPending(scope));
    void queryClient.invalidateQueries({ queryKey: customerReturnsKeys.all });
  };
  return (
    <section aria-label="Смешанные возвраты в карантине" className="space-y-4">
      {canView ? (
        <div
          role="group"
          aria-label="Состояние смешанного карантина"
          className="flex flex-wrap gap-2"
        >
          <Button
            variant={view === "pending" ? "primary" : "secondary"}
            aria-pressed={view === "pending"}
            onClick={() => setView("pending")}
          >
            В карантине
          </Button>
          <Button
            variant={view === "history" ? "primary" : "secondary"}
            aria-pressed={view === "history"}
            onClick={() => setView("history")}
          >
            История решений
          </Button>
        </div>
      ) : null}
      {storage.error ? (
        <p role="alert" className="rounded-lg border border-danger/30 p-4 text-sm text-danger">
          {storage.error}
        </p>
      ) : null}
      {storage.pending ? (
        <div className="space-y-3 rounded-lg border border-warning/40 p-4">
          <p>
            Есть сохранённое решение, результат которого нужно проверить. До подтверждения новые
            решения недоступны.
          </p>
          <Button
            variant="secondary"
            disabled={
              !usable ||
              !hasPermissionForBranch(user, "customer_returns.resolve", storage.pending.branchId)
            }
            onClick={() => setDialog({ item: null, pending: storage.pending })}
          >
            Проверить сохранённое решение
          </Button>
        </div>
      ) : null}
      {!canView ? (
        <p>
          Здесь можно проверить ранее сохранённое смешанное решение. Для очереди и истории нужно
          право просмотра возвратов.
        </p>
      ) : view === "pending" ? (
        children?.({ onResolve, canResolve, blocked })
      ) : scope ? (
        <MixedDispositionHistoryPanel scope={scope} />
      ) : (
        <p role="alert">Выберите действующий аккаунт аптеки.</p>
      )}
      {dialog && scope ? (
        <MixedDispositionModal
          scope={scope}
          item={dialog.item}
          pending={dialog.pending}
          onResolved={refresh}
          onClose={() => {
            setDialog(null);
            refresh();
          }}
        />
      ) : null}
    </section>
  );
}
