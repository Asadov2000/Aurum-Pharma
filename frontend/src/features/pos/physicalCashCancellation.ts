import { api } from "@/lib/api";

import { removeStoredValue } from "./operationStorage";
import {
  cashCheckoutContracts,
  getPhysicalCashOperation,
  parsePhysicalCashOperationStatus,
  type PhysicalCashOperationStatus,
} from "./physicalCashCheckout";
import { loadPendingPhysicalCash, type PendingPhysicalCash } from "./physicalCashRecovery";
import { hasMixedCashRecovery, physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";
import { capturePosSessionGuard } from "./posStorageScope";

type Terminal = Extract<PhysicalCashOperationStatus, { state: "cancelled" | "completed" }>;

async function reconcile(
  pending: PendingPhysicalCash,
  active: () => boolean,
  cancel: boolean,
  signal?: AbortSignal,
): Promise<Terminal | null> {
  const scope = { tenantId: pending.tenantId, userId: pending.userId };
  const guard = capturePosSessionGuard(scope);
  const saleId = pending.request.sale_id;
  const key = physicalCashRecoveryKey(saleId, scope);
  const fixed = JSON.stringify(pending);
  function current(): void {
    if (!guard() || !active() || signal?.aborted)
      throw new Error("Условия проверки оплаты изменились. Откройте кассу заново.");
    if (
      hasMixedCashRecovery(saleId, scope) ||
      JSON.stringify(loadPendingPhysicalCash(saleId, scope)) !== fixed
    )
      throw new Error("Сохранённая оплата изменилась. Проверьте её состояние.");
  }
  current();
  if (!navigator.locks?.request)
    throw new Error("Для безопасной отмены откройте кассу в современном браузере.");
  return navigator.locks.request(key, async () => {
    current();
    let status = await getPhysicalCashOperation(pending.request, scope, signal);
    current();
    if (cancel && status.state === "saved_draft") {
      const { data } = await api.post<unknown>(
        `/sales/${encodeURIComponent(saleId)}/physical-cash/cancel`,
        pending.request,
        { signal, timeout: 8_000 },
      );
      current();
      const response = parsePhysicalCashOperationStatus(data, pending.request, scope);
      if (response.state !== "completed" && response.state !== "cancelled")
        throw new Error("Сервер не подтвердил завершение или отмену оплаты.");
      if (
        (await cashCheckoutContracts.quoteHash(response.saved_intent.confirmation.quote)) !==
        pending.request.expected_revision_hash
      )
        throw new Error("Сохранённая корзина не соответствует подтверждённой оплате.");
      current();
      // A completed response is never discarded, even if another writer won the race.
      if (response.state === "completed") return response;
      // Only a fresh authoritative terminal GET permits local acknowledgement.
      status = await getPhysicalCashOperation(pending.request, scope, signal);
      current();
    }
    if (status.state === "completed") return status;
    if (status.state !== "cancelled") {
      if (cancel)
        throw new Error(
          "Отмена оплаты не подтверждена. Сохранённая оплата останется на устройстве.",
        );
      return null;
    }
    current();
    if (!removeStoredValue(key))
      throw new Error("Не удалось сохранить подтверждение отмены. Проверьте оплату повторно.");
    return status;
  });
}

export function cancelPendingPhysicalCash(
  pending: PendingPhysicalCash,
  active: () => boolean,
  signal?: AbortSignal,
): Promise<Terminal | null> {
  return reconcile(pending, active, true, signal);
}

/** Read-only recovery: never sends cancellation or completion automatically. */
export function acknowledgeCancelledPhysicalCash(
  pending: PendingPhysicalCash,
  active: () => boolean,
  signal?: AbortSignal,
): Promise<Terminal | null> {
  return reconcile(pending, active, false, signal);
}
