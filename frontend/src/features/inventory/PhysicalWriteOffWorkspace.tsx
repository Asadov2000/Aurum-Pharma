import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { hasPermissionForBranch } from "@/features/auth/branchPermissions";
import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import { describeApiError } from "@/features/foundation/errors";
import { useConnectivity } from "@/lib/connectivityContext";
import { type PhysicalLot } from "./physicalLots";
import { type PhysicalWriteoffInput, type PhysicalWriteoffResult } from "./physicalWriteoff";
import {
  acknowledgePhysicalWriteoff,
  inspectPhysicalWriteoff,
  loadPhysicalWriteoff,
  runPhysicalWriteoff,
} from "./physicalWriteoffRecovery";
import { PhysicalWriteOffModal } from "./PhysicalWriteOffModal";

export function PhysicalWriteOffWorkspace({
  lot,
  writable,
  onClose,
  onBarrier,
}: {
  lot: PhysicalLot | null;
  writable: boolean;
  onClose: () => void;
  onBarrier: (blocked: boolean) => void;
}): JSX.Element | null {
  const { user } = useAuth();
  const tenantId = user?.active_tenant_id ?? "";
  const userId = user?.id ?? "";
  const [guard] = useState(() => capturePosSessionGuard({ tenantId, userId }));
  const online = useConnectivity().canUseServer;
  const client = useQueryClient();
  const [pending, setPending] = useState<ReturnType<typeof loadPhysicalWriteoff>>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PhysicalWriteoffResult | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const read = useCallback(() => {
    if (!guard()) return;
    try {
      const stored = loadPhysicalWriteoff({ tenantId, userId });
      setPending(stored);
      setResult((current) =>
        current?.operation_id === stored?.request.operation_id ? current : null,
      );
      setStorageError(null);
      onBarrier(stored !== null);
    } catch (cause) {
      setStorageError(describeApiError(cause, "Не удалось прочитать сохранённое списание."));
      onBarrier(true);
    }
  }, [guard, tenantId, userId, onBarrier]);
  useEffect(() => {
    read();
    window.addEventListener("storage", read);
    window.addEventListener("focus", read);
    return () => {
      window.removeEventListener("storage", read);
      window.removeEventListener("focus", read);
    };
  }, [read]);
  const allowed = (branchId: string) =>
    !user?.support_access &&
    hasPermissionForBranch(user, "batches.view", branchId) &&
    hasPermissionForBranch(user, "batches.write_off", branchId);
  const refresh = async () => {
    await Promise.all(
      ["inventory", "reports", "pos"].map((key) => client.invalidateQueries({ queryKey: [key] })),
    );
  };
  async function execute(
    action: () => Promise<PhysicalWriteoffResult | null | void>,
  ): Promise<void> {
    if (inFlight.current || !guard() || !online) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const confirmed = await action();
      if (!guard()) return;
      setResult(confirmed ?? null);
      if (confirmed) await refresh();
    } catch (cause) {
      if (guard()) {
        setResult(null);
        setError(
          describeApiError(cause, "Результат не подтверждён. Проверьте его перед продолжением."),
        );
      }
    } finally {
      inFlight.current = false;
      if (guard()) {
        setBusy(false);
        read();
      }
    }
  }
  async function submit(input: PhysicalWriteoffInput, total: string): Promise<void> {
    if (!writable || !allowed(input.branch_id) || storageError) return;
    await execute(() =>
      runPhysicalWriteoff({ tenantId, userId, branchId: input.branch_id }, input, total),
    );
    if (guard()) onClose();
  }
  const scope = pending ? { tenantId, userId, branchId: pending.request.branch_id } : null;
  return (
    <>
      {storageError && (
        <div role="alert">
          <p>{storageError}</p>
          <Button onClick={read}>Проверить хранилище</Button>
        </div>
      )}
      {pending && (
        <section aria-label="Результат списания" className="space-y-3 rounded-lg border p-4">
          <p>Списание: {pending.request.operation_id}</p>
          <p>
            Аптека: {pending.request.branch_id} · Партия: {pending.request.lot_id}
          </p>
          {result ? (
            <p role="status">Списание выполнено.</p>
          ) : (
            <p>
              Сохранена команда списания. До подтверждения результата новое списание недоступно.
            </p>
          )}
          {result && <p>Списано: {result.base_units} базовых единиц.</p>}
          {error && <p role="alert">{error}</p>}
          <Button
            disabled={busy || !online || !allowed(pending.request.branch_id)}
            onClick={() => {
              if (scope) void execute(() => inspectPhysicalWriteoff(scope));
            }}
          >
            Проверить результат
          </Button>
          {!result && (
            <Button
              disabled={busy || !online || !writable || !allowed(pending.request.branch_id)}
              onClick={() => {
                if (scope) void execute(() => runPhysicalWriteoff(scope));
              }}
            >
              Повторить сохранённое списание
            </Button>
          )}
          {result && (
            <Button
              disabled={busy || !online || !allowed(pending.request.branch_id)}
              onClick={() => {
                if (scope) void execute(() => acknowledgePhysicalWriteoff(scope));
              }}
            >
              Готово
            </Button>
          )}
        </section>
      )}
      {!pending && error && <p role="alert">{error}</p>}
      {lot && !pending && !storageError && (
        <PhysicalWriteOffModal
          lot={lot}
          writable={writable && online && allowed(lot.branch_id)}
          onClose={onClose}
          onSubmit={submit}
        />
      )}
    </>
  );
}
