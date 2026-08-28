import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  SkeletonRows,
} from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/features/foundation/errors";

import {
  useAcceptOwnershipTransfer,
  useCancelOwnershipTransfer,
  useOwnershipTransfersQuery,
} from "./queries";
import { type OwnershipTransfer, type OwnershipTransferStatus } from "./types";

type PendingAction = { type: "accept" | "cancel"; transfer: OwnershipTransfer };

const statusLabel: Record<OwnershipTransferStatus, string> = {
  pending: "Ожидает",
  completed: "Передано",
  cancelled: "Отменено",
  expired: "Истёк",
};

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

function statusTone(status: OwnershipTransferStatus): "success" | "warning" | "neutral" {
  if (status === "completed") return "success";
  if (status === "pending") return "warning";
  return "neutral";
}

export function OwnershipTransferPanel(): JSX.Element | null {
  const { user, logout } = useAuth();
  const tenantId = activeTenantId(user);
  const transfers = useOwnershipTransfersQuery(Boolean(tenantId));
  const accept = useAcceptOwnershipTransfer();
  const cancel = useCancelOwnershipTransfer();
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!tenantId) return null;

  if (transfers.isLoading) {
    return (
      <Card>
        <CardContent>
          <SkeletonRows rows={2} />
        </CardContent>
      </Card>
    );
  }

  if (transfers.error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Владение аптекой</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-danger" role="alert">
            {describeApiError(transfers.error, "Не удалось проверить запросы передачи владения")}
          </p>
          <Button type="button" variant="secondary" onClick={() => void transfers.refetch()}>
            Повторить
          </Button>
        </CardContent>
      </Card>
    );
  }

  const items = transfers.data ?? [];
  if (items.length === 0) return null;

  const isMutating = accept.isPending || cancel.isPending;

  const runAction = async () => {
    if (!pendingAction) return;
    setActionError(null);
    try {
      if (pendingAction.type === "accept") {
        await accept.mutateAsync(pendingAction.transfer.id);
        setPendingAction(null);
        await logout();
        return;
      }
      await cancel.mutateAsync(pendingAction.transfer.id);
      setPendingAction(null);
    } catch (error) {
      setActionError(
        describeApiError(
          error,
          pendingAction.type === "accept"
            ? "Не удалось подтвердить передачу владения"
            : "Не удалось отменить передачу владения",
        ),
      );
    }
  };

  return (
    <>
      <Card data-testid="ownership-transfer-panel">
        <CardHeader>
          <CardTitle>Владение аптекой</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {actionError ? (
            <p className="mx-5 mb-3 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground" role="alert">
              {actionError}
            </p>
          ) : null}
          <div className="divide-y divide-border">
            {items.slice(0, 5).map((transfer) => {
              const isTarget = transfer.target_user_id === user?.id;
              const isInitiator = transfer.initiator_user_id === user?.id;
              return (
                <div
                  key={transfer.id}
                  className="grid gap-4 px-5 py-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium text-foreground">
                        {isTarget
                          ? `От ${transfer.initiator_full_name}`
                          : `Для ${transfer.target_full_name}`}
                      </p>
                      <Badge tone={statusTone(transfer.status)}>
                        {statusLabel[transfer.status]}
                      </Badge>
                    </div>
                    <p className="mt-1 text-sm text-foreground-secondary">
                      {transfer.status === "pending"
                        ? isTarget
                          ? "Вам предложено принять управление аптекой."
                          : "Сотрудник должен подтвердить передачу в своём аккаунте."
                        : `Создано ${formatDate(transfer.created_at)}`}
                    </p>
                    {transfer.status === "pending" ? (
                      <p className="mt-1 text-xs text-foreground-muted">
                        Действует до {formatDate(transfer.expires_at)}
                      </p>
                    ) : null}
                  </div>
                  {transfer.status === "pending" && (isTarget || isInitiator) ? (
                    <Button
                      type="button"
                      variant={isTarget ? "primary" : "secondary"}
                      disabled={isMutating}
                      onClick={() => {
                        setActionError(null);
                        setPendingAction({ type: isTarget ? "accept" : "cancel", transfer });
                      }}
                    >
                      {isTarget ? "Принять владение" : "Отменить запрос"}
                    </Button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={pendingAction !== null}
        title={
          pendingAction?.type === "accept"
            ? "Стать владельцем аптеки?"
            : "Отменить передачу владения?"
        }
        message={
          pendingAction?.type === "accept"
            ? "Вы получите управление аптекой, доступ прежнего владельца будет снят. Все сеансы обоих аккаунтов завершатся, затем потребуется войти снова."
            : "Запрос будет закрыт. Текущий владелец сохранит доступ без изменений."
        }
        confirmLabel={pendingAction?.type === "accept" ? "Принять владение" : "Отменить запрос"}
        variant="danger"
        isLoading={isMutating}
        onConfirm={() => void runAction()}
        onCancel={() => {
          if (isMutating) return;
          setPendingAction(null);
          setActionError(null);
        }}
      />
    </>
  );
}
