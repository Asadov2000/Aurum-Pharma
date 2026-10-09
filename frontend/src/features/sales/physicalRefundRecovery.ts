import { isAxiosError } from "axios";
import { z } from "zod";

import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";

import {
  completePhysicalRefund,
  getPhysicalRefundStatus,
  physicalRefundRequestSchema,
  type PhysicalRefundCompleted,
  type PhysicalRefundInput,
  type PhysicalRefundStatus,
} from "./physicalRefund";

export interface PhysicalRefundScope extends PosStorageScope {
  parentSaleId: string;
  branchId: string;
  registerId: string;
}
const pendingSchema = z
  .object({
    version: z.literal(1),
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    parentSaleId: z.string().uuid(),
    branchId: z.string().uuid(),
    registerId: z.string().uuid(),
    request: physicalRefundRequestSchema,
    definiteRejected: z.boolean().default(false),
  })
  .strict();
export type PendingPhysicalRefund = z.infer<typeof pendingSchema>;
const rejectionSchema = z.object({ error: z.object({ code: z.string(), message: z.string() }) });
function definitiveRejection(error: unknown): boolean {
  if (!isAxiosError(error)) return false;
  const envelope = rejectionSchema.safeParse(error.response?.data);
  if (!envelope.success) return false;
  const status = error.response?.status;
  const code = envelope.data.error.code;
  return (
    (status === 409 && code === "conflict") ||
    (status === 422 && (code === "business_rule_violation" || code === "validation_error"))
  );
}

export function physicalRefundKey(scope: PhysicalRefundScope): string {
  return `sales:physicalRefund:v1:${encodeURIComponent(
    JSON.stringify([
      scope.tenantId,
      scope.userId,
      scope.branchId,
      scope.registerId,
      scope.parentSaleId,
    ]),
  )}`;
}
export function loadPhysicalRefund(scope: PhysicalRefundScope): PendingPhysicalRefund | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(physicalRefundKey(scope));
  } catch {
    throw new Error("Не удалось прочитать сохранённый возврат. Проверьте хранилище.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (
      pending.tenantId !== scope.tenantId ||
      pending.userId !== scope.userId ||
      pending.parentSaleId !== scope.parentSaleId ||
      pending.branchId !== scope.branchId ||
      pending.registerId !== scope.registerId
    )
      throw new Error("Scope mismatch");
    return pending;
  } catch {
    throw new Error(
      "Сохранённый возврат повреждён. Не выдавайте деньги повторно; обратитесь к администратору.",
    );
  }
}
function assertCurrent(
  pending: PendingPhysicalRefund,
  scope: PhysicalRefundScope,
  active: () => boolean,
): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
  const stored = loadPhysicalRefund(scope);
  if (!stored || JSON.stringify(stored) !== JSON.stringify(pending))
    throw new Error("Сохранённая операция изменилась. Проверьте возврат заново.");
}
async function exclusively<T>(
  scope: PhysicalRefundScope,
  execute: (active: () => boolean) => Promise<T>,
): Promise<T> {
  const active = capturePosSessionGuard(scope);
  if (!active()) throw new Error("Войдите в аккаунт кассира.");
  if (!navigator.locks?.request)
    throw new Error("Для безопасного возврата используйте приложение или современный браузер.");
  return navigator.locks.request(physicalRefundKey(scope), async () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
    return execute(active);
  });
}

/** Opening/reloading the dialog can inspect an existing operation, but never sends a payout. */
export async function inspectPhysicalRefund(
  scope: PhysicalRefundScope,
): Promise<PhysicalRefundStatus | null> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalRefund(scope);
    if (!pending) return null;
    assertCurrent(pending, scope, active);
    const status = await getPhysicalRefundStatus(scope.parentSaleId, pending.request, scope);
    assertCurrent(pending, scope, active);
    return status;
  });
}

/** Only an explicit user confirmation calls this function; retries retain the exact request. */
export async function runPhysicalRefund(
  scope: PhysicalRefundScope,
  input?: PhysicalRefundInput,
): Promise<PhysicalRefundCompleted> {
  return exclusively(scope, async (active) => {
    let pending = loadPhysicalRefund(scope);
    if (!pending) {
      if (!input) throw new Error("Сохранённого возврата нет.");
      pending = pendingSchema.parse({
        version: 1,
        ...scope,
        request: physicalRefundRequestSchema.parse({ ...input, operation_id: generateUuidV4() }),
      });
      if (!writeStoredJson(physicalRefundKey(scope), pending))
        throw new Error("Не удалось сохранить возврат. Операция не отправлена.");
    } else if (input) {
      const candidate = physicalRefundRequestSchema.parse({
        ...input,
        operation_id: pending.request.operation_id,
      });
      if (JSON.stringify(candidate) !== JSON.stringify(pending.request))
        throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
    }
    assertCurrent(pending, scope, active);
    const status = await getPhysicalRefundStatus(scope.parentSaleId, pending.request, scope);
    assertCurrent(pending, scope, active);
    if (status.state === "completed") return status;
    if (pending.definiteRejected) {
      pending = { ...pending, definiteRejected: false };
      if (!writeStoredJson(physicalRefundKey(scope), pending))
        throw new Error("Не удалось сохранить повтор возврата. Операция не отправлена.");
      assertCurrent(pending, scope, active);
    }
    try {
      const completed = await completePhysicalRefund(scope.parentSaleId, pending.request, scope);
      assertCurrent(pending, scope, active);
      return completed;
    } catch (error) {
      if (definitiveRejection(error)) {
        assertCurrent(pending, scope, active);
        if (!writeStoredJson(physicalRefundKey(scope), { ...pending, definiteRejected: true }))
          throw new Error("Не удалось сохранить отказ сервера. Проверьте результат возврата.", {
            cause: error,
          });
      }
      throw error;
    }
  });
}

/** Explicit recalculation only follows a verified server rejection, never a lost response. */
export async function resetRejectedPhysicalRefund(
  scope: PhysicalRefundScope,
): Promise<PhysicalRefundCompleted | { state: "cleared" }> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalRefund(scope);
    if (!pending?.definiteRejected)
      throw new Error("Результат возврата неизвестен. Сначала проверьте эту же операцию.");
    const status = await getPhysicalRefundStatus(scope.parentSaleId, pending.request, scope);
    assertCurrent(pending, scope, active);
    if (status.state === "completed") return status;
    if (!removeStoredValue(physicalRefundKey(scope)))
      throw new Error("Не удалось закрыть отклонённый запрос. Новую операцию не отправляйте.");
    return { state: "cleared" };
  });
}

/** Acknowledgment rechecks the server; unknown outcomes and damaged records are retained. */
export async function acknowledgePhysicalRefund(
  scope: PhysicalRefundScope,
  operationId: string,
): Promise<boolean> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalRefund(scope);
    if (!pending || pending.request.operation_id !== operationId) return false;
    const status = await getPhysicalRefundStatus(scope.parentSaleId, pending.request, scope);
    assertCurrent(pending, scope, active);
    return status.state === "completed" && removeStoredValue(physicalRefundKey(scope));
  });
}
