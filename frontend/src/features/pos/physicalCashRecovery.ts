import { z } from "zod";

import { generateUuidV4 } from "./operationId";
import { removeStoredValue, writeStoredJson } from "./operationStorage";
import {
  cashConfirmationRequestSchema,
  completePhysicalCash,
  getPhysicalCashOperation,
  savePhysicalCashIntent,
  type CashConfirmationRequest,
  type PhysicalCashCheckoutResult,
} from "./physicalCashCheckout";
import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";
import { physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";

export { physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";

const pendingSchema = z
  .object({
    version: z.literal(1),
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    createdAt: z.number().int().positive().safe(),
    request: cashConfirmationRequestSchema,
  })
  .strict();

export type PendingPhysicalCash = z.infer<typeof pendingSchema>;
export type PhysicalCashInput = Omit<CashConfirmationRequest, "operation_id">;

/** Never expire or delete an unreadable financial intent: its outcome may be committed. */
export function loadPendingPhysicalCash(
  saleId: string,
  scope: PosStorageScope,
): PendingPhysicalCash | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(physicalCashRecoveryKey(saleId, scope));
  } catch {
    throw new Error("Не удалось прочитать сохранённую оплату. Проверьте доступ к хранилищу.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (
      pending.tenantId !== scope.tenantId ||
      pending.userId !== scope.userId ||
      pending.request.sale_id !== saleId
    )
      throw new Error("scope mismatch");
    return pending;
  } catch {
    throw new Error(
      "Сохранённая оплата повреждена. Не принимайте оплату повторно; обратитесь к администратору.",
    );
  }
}

function assertCurrent(
  pending: PendingPhysicalCash,
  scope: PosStorageScope,
  active: () => boolean,
): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте кассу заново.");
  const stored = loadPendingPhysicalCash(pending.request.sale_id, scope);
  if (JSON.stringify(stored) !== JSON.stringify(pending))
    throw new Error("Сохранённая оплата изменилась. Проверьте её состояние.");
}

async function exclusively<T>(
  saleId: string,
  scope: PosStorageScope,
  execute: (active: () => boolean) => Promise<T>,
): Promise<T> {
  const active = capturePosSessionGuard(scope);
  if (!active()) throw new Error("Войдите в аккаунт кассира.");
  // Serialize creation as well as requests across tabs. Without an atomic browser
  // lock, two tabs could persist different UUIDs before either sees the other.
  if (!navigator.locks?.request)
    throw new Error("Для безопасной оплаты откройте кассу в приложении или современном браузере.");
  return navigator.locks.request(physicalCashRecoveryKey(saleId, scope), async () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте кассу заново.");
    return execute(active);
  });
}

/** Persist first, then inspect the server before every attempt, including reload recovery. */
export async function runPhysicalCash(
  saleId: string,
  scope: PosStorageScope,
  input?: PhysicalCashInput,
): Promise<PhysicalCashCheckoutResult> {
  return exclusively(saleId, scope, async (active) => {
    let pending = loadPendingPhysicalCash(saleId, scope);
    if (!pending) {
      if (!input) throw new Error("Сохранённой оплаты нет.");
      const request = cashConfirmationRequestSchema.parse({
        ...input,
        operation_id: generateUuidV4(),
      });
      if (request.sale_id !== saleId) throw new Error("Оплата относится к другому чеку.");
      pending = pendingSchema.parse({ version: 1, ...scope, createdAt: Date.now(), request });
      if (!writeStoredJson(physicalCashRecoveryKey(saleId, scope), pending))
        throw new Error(
          "Не удалось сохранить оплату. Операция не отправлена; проверьте хранилище.",
        );
    } else if (input) {
      const request = cashConfirmationRequestSchema.parse({
        ...input,
        operation_id: pending.request.operation_id,
      });
      if (JSON.stringify(request) !== JSON.stringify(pending.request))
        throw new Error("Есть незавершённая оплата. Сначала проверьте её результат.");
    }
    assertCurrent(pending, scope, active);
    const status = await getPhysicalCashOperation(pending.request, scope);
    assertCurrent(pending, scope, active);
    if (status.state === "completed") return status.result;
    if (status.state === "not_found") {
      await savePhysicalCashIntent(pending.request, scope);
      assertCurrent(pending, scope, active);
    }
    await completePhysicalCash(pending.request, scope);
    assertCurrent(pending, scope, active);
    const completed = await getPhysicalCashOperation(pending.request, scope);
    assertCurrent(pending, scope, active);
    if (completed.state !== "completed")
      throw new Error("Результат оплаты ещё не подтверждён. Проверьте эту же операцию повторно.");
    // Keep the intent until the caller has displayed/recorded the completed sale.
    return completed.result;
  });
}

/** Explicit acknowledgment after UI completion; unknown/saved results never clear the queue.
 * The caller must check its own mounted/session guard before invoking this action.
 */
export async function acknowledgePhysicalCash(
  saleId: string,
  operationId: string,
  scope: PosStorageScope,
): Promise<boolean> {
  return exclusively(saleId, scope, async (active) => {
    const pending = loadPendingPhysicalCash(saleId, scope);
    if (!pending || pending.request.operation_id !== operationId) return false;
    const status = await getPhysicalCashOperation(pending.request, scope);
    assertCurrent(pending, scope, active);
    return (
      status.state === "completed" && removeStoredValue(physicalCashRecoveryKey(saleId, scope))
    );
  });
}
