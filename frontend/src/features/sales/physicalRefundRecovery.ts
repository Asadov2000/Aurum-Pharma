import { isAxiosError } from "axios";
import { z } from "zod";

import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";

import {
  completePhysicalRefund,
  completePhysicalZeroReturn,
  getPhysicalRefundStatus,
  getPhysicalZeroReturnStatus,
  physicalRefundRequestSchema,
  physicalZeroReturnRequestSchema,
  type PhysicalRefundCompleted,
  type PhysicalRefundInput,
  type PhysicalRefundQuote,
  type PhysicalReturnResult,
  type PhysicalReturnStatus,
  type PhysicalZeroReturnInput,
  type PhysicalZeroReturnPrepared,
} from "./physicalRefund";

export interface PhysicalRefundScope extends PosStorageScope {
  parentSaleId: string;
  branchId: string;
  registerId: string;
}
const pendingScopeShape = {
  version: z.literal(1),
  tenantId: z.string().uuid(),
  userId: z.string().uuid(),
  parentSaleId: z.string().uuid(),
  branchId: z.string().uuid(),
  registerId: z.string().uuid(),
  definiteRejected: z.boolean().default(false),
};
const cashPendingSchema = z
  .object({
    ...pendingScopeShape,
    request: physicalRefundRequestSchema,
  })
  .strict();
const zeroPendingSchema = z
  .object({
    ...pendingScopeShape,
    kind: z.literal("physical_zero_return"),
    request: physicalZeroReturnRequestSchema,
  })
  .strict();
const pendingSchema = z.union([cashPendingSchema, zeroPendingSchema]);
export type PendingPhysicalRefund = z.infer<typeof pendingSchema>;
export function isZeroPhysicalReturn(
  pending: PendingPhysicalRefund,
): pending is z.infer<typeof zeroPendingSchema> {
  return "kind" in pending && pending.kind === "physical_zero_return";
}
async function inspectPending(
  pending: PendingPhysicalRefund,
  scope: PhysicalRefundScope,
): Promise<PhysicalReturnStatus> {
  return isZeroPhysicalReturn(pending)
    ? getPhysicalZeroReturnStatus(scope.parentSaleId, pending.request, scope)
    : getPhysicalRefundStatus(scope.parentSaleId, pending.request, scope);
}
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
): Promise<PhysicalReturnStatus | null> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalRefund(scope);
    if (!pending) return null;
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    return status;
  });
}

/** Only an explicit user confirmation calls this function; retries retain the exact request. */
export async function runPhysicalRefund(
  scope: PhysicalRefundScope,
  input?: PhysicalRefundInput,
): Promise<PhysicalRefundCompleted> {
  const result = await runPending(scope, input ? { kind: "cash", input } : undefined, "cash");
  if (result.state !== "completed") throw new Error("Ожидался денежный возврат.");
  return result;
}
export async function runPhysicalZeroReturn(
  scope: PhysicalRefundScope,
  input?: PhysicalZeroReturnInput,
  reviewedQuote?: PhysicalRefundQuote,
): Promise<PhysicalZeroReturnPrepared> {
  const result = await runPending(
    scope,
    input ? { kind: "physical_zero_return", input } : undefined,
    "physical_zero_return",
    reviewedQuote,
  );
  if (result.state !== "prepared_quarantine") throw new Error("Ожидался возврат без выплаты.");
  return result;
}
type NewPhysicalReturn =
  | { kind: "cash"; input: PhysicalRefundInput }
  | { kind: "physical_zero_return"; input: PhysicalZeroReturnInput };
async function runPending(
  scope: PhysicalRefundScope,
  candidateInput?: NewPhysicalReturn,
  expectedKind?: "cash" | "physical_zero_return",
  reviewedQuote?: PhysicalRefundQuote,
): Promise<PhysicalReturnResult> {
  return exclusively(scope, async (active) => {
    let pending = loadPhysicalRefund(scope);
    if (!pending) {
      if (!candidateInput) throw new Error("Сохранённого возврата нет.");
      const request = {
        ...candidateInput.input,
        operation_id: generateUuidV4(),
      };
      pending = pendingSchema.parse({
        version: 1,
        ...scope,
        ...(candidateInput.kind === "physical_zero_return"
          ? { kind: candidateInput.kind, request: physicalZeroReturnRequestSchema.parse(request) }
          : { request: physicalRefundRequestSchema.parse(request) }),
      });
      if (!writeStoredJson(physicalRefundKey(scope), pending))
        throw new Error("Не удалось сохранить возврат. Операция не отправлена.");
    } else if (candidateInput) {
      const actualKind = isZeroPhysicalReturn(pending) ? "physical_zero_return" : "cash";
      if (actualKind !== candidateInput.kind)
        throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
      const request = {
        ...candidateInput.input,
        operation_id: pending.request.operation_id,
      };
      const candidate =
        candidateInput.kind === "physical_zero_return"
          ? physicalZeroReturnRequestSchema.parse(request)
          : physicalRefundRequestSchema.parse(request);
      if (JSON.stringify(candidate) !== JSON.stringify(pending.request))
        throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
    }
    if (
      expectedKind &&
      expectedKind !== (isZeroPhysicalReturn(pending) ? "physical_zero_return" : "cash")
    )
      throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    if (status.state !== "not_found") return status;
    if (pending.definiteRejected) {
      pending = { ...pending, definiteRejected: false };
      if (!writeStoredJson(physicalRefundKey(scope), pending))
        throw new Error("Не удалось сохранить повтор возврата. Операция не отправлена.");
      assertCurrent(pending, scope, active);
    }
    try {
      const completed = isZeroPhysicalReturn(pending)
        ? await completePhysicalZeroReturn(
            scope.parentSaleId,
            pending.request,
            scope,
            reviewedQuote,
          )
        : await completePhysicalRefund(scope.parentSaleId, pending.request, scope);
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
): Promise<PhysicalReturnResult | { state: "cleared" }> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalRefund(scope);
    if (!pending?.definiteRejected)
      throw new Error("Результат возврата неизвестен. Сначала проверьте эту же операцию.");
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    if (status.state !== "not_found") return status;
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
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    return status.state !== "not_found" && removeStoredValue(physicalRefundKey(scope));
  });
}
