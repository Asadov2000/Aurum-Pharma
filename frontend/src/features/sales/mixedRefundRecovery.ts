import { isAxiosError } from "axios";
import { z } from "zod";

import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";

import {
  completeMixedRefund,
  completeMixedZeroReturn,
  getMixedRefundStatus,
  getMixedZeroReturnStatus,
  mixedRefundRequestSchema,
  mixedZeroReturnRequestSchema,
  validateMixedZeroReturnQuote,
  type MixedRefundCompleted,
  type MixedRefundInput,
  type MixedRefundQuote,
  type MixedReturnResult,
  type MixedReturnStatus,
  type MixedZeroReturnInput,
  type MixedZeroReturnPrepared,
} from "./mixedRefund";

export interface MixedRefundScope extends PosStorageScope {
  parentSaleId: string;
  branchId: string;
  registerId: string;
}
const pendingScopeShape = {
  tenantId: z.string().uuid(),
  userId: z.string().uuid(),
  parentSaleId: z.string().uuid(),
  branchId: z.string().uuid(),
  registerId: z.string().uuid(),
  definiteRejected: z.boolean().default(false),
};
// Existing cash v1 records retain their shape and key across this upgrade.
const cashPendingSchema = z
  .object({ version: z.literal(1), ...pendingScopeShape, request: mixedRefundRequestSchema })
  .strict();
const zeroPendingSchema = z
  .object({
    version: z.literal(2),
    kind: z.literal("mixed_zero_return"),
    ...pendingScopeShape,
    request: mixedZeroReturnRequestSchema,
  })
  .strict();
const pendingSchema = z.union([cashPendingSchema, zeroPendingSchema]);
export type PendingMixedRefund = z.infer<typeof pendingSchema>;
export function isZeroMixedReturn(
  pending: PendingMixedRefund,
): pending is z.infer<typeof zeroPendingSchema> {
  return pending.version === 2 && "kind" in pending && pending.kind === "mixed_zero_return";
}
async function inspectPending(
  pending: PendingMixedRefund,
  scope: MixedRefundScope,
): Promise<MixedReturnStatus> {
  return isZeroMixedReturn(pending)
    ? getMixedZeroReturnStatus(scope.parentSaleId, pending.request, scope)
    : getMixedRefundStatus(scope.parentSaleId, pending.request, scope);
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
export function mixedRefundKey(scope: MixedRefundScope): string {
  return `sales:mixedRefund:v1:${encodeURIComponent(JSON.stringify([scope.tenantId, scope.userId, scope.branchId, scope.registerId, scope.parentSaleId]))}`;
}
export function loadMixedRefund(scope: MixedRefundScope): PendingMixedRefund | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(mixedRefundKey(scope));
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
  pending: PendingMixedRefund,
  scope: MixedRefundScope,
  active: () => boolean,
): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
  const stored = loadMixedRefund(scope);
  if (!stored || JSON.stringify(stored) !== JSON.stringify(pending))
    throw new Error("Сохранённая операция изменилась. Проверьте возврат заново.");
}
async function exclusively<T>(
  scope: MixedRefundScope,
  execute: (active: () => boolean) => Promise<T>,
): Promise<T> {
  const active = capturePosSessionGuard(scope);
  if (!active()) throw new Error("Войдите в аккаунт кассира.");
  if (!navigator.locks?.request)
    throw new Error("Для безопасного возврата используйте приложение или современный браузер.");
  return navigator.locks.request(mixedRefundKey(scope), async () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
    return execute(active);
  });
}
/** Reloading and opening the dialog only inspect an operation; they never issue a payout. */
export async function inspectMixedRefund(
  scope: MixedRefundScope,
): Promise<MixedReturnStatus | null> {
  return exclusively(scope, async (active) => {
    const pending = loadMixedRefund(scope);
    if (!pending) return null;
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    return status;
  });
}
/** Explicit confirmation only. Retries retain the same reviewed request and inventory kind. */
export async function runMixedRefund(
  scope: MixedRefundScope,
  input?: MixedRefundInput,
): Promise<MixedRefundCompleted> {
  const result = await runPending(scope, "cash", input ? { kind: "cash", input } : undefined);
  if (result.state !== "completed") throw new Error("Ожидался денежный возврат.");
  return result;
}
export async function runMixedZeroReturn(
  scope: MixedRefundScope,
  input?: MixedZeroReturnInput,
  reviewedQuote?: MixedRefundQuote,
): Promise<MixedZeroReturnPrepared> {
  if (input && reviewedQuote)
    validateMixedZeroReturnQuote(scope.parentSaleId, input, reviewedQuote);
  const result = await runPending(
    scope,
    "mixed_zero_return",
    input ? { kind: "mixed_zero_return", input } : undefined,
    reviewedQuote,
  );
  if (result.state !== "prepared_quarantine") throw new Error("Ожидался возврат без выплаты.");
  return result;
}
type NewMixedReturn =
  | { kind: "cash"; input: MixedRefundInput }
  | { kind: "mixed_zero_return"; input: MixedZeroReturnInput };
async function runPending(
  scope: MixedRefundScope,
  expectedKind: "cash" | "mixed_zero_return",
  candidateInput?: NewMixedReturn,
  reviewedQuote?: MixedRefundQuote,
): Promise<MixedReturnResult> {
  return exclusively(scope, async (active) => {
    let pending = loadMixedRefund(scope);
    if (!pending) {
      if (!candidateInput) throw new Error("Сохранённого возврата нет.");
      const request = { ...candidateInput.input, operation_id: generateUuidV4() };
      pending = pendingSchema.parse(
        candidateInput.kind === "cash"
          ? { version: 1, ...scope, request: mixedRefundRequestSchema.parse(request) }
          : {
              version: 2,
              kind: "mixed_zero_return",
              ...scope,
              request: mixedZeroReturnRequestSchema.parse(request),
            },
      );
      if (!writeStoredJson(mixedRefundKey(scope), pending))
        throw new Error("Не удалось сохранить возврат. Операция не отправлена.");
    } else if (candidateInput) {
      if ((isZeroMixedReturn(pending) ? "mixed_zero_return" : "cash") !== candidateInput.kind)
        throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
      const request = { ...candidateInput.input, operation_id: pending.request.operation_id };
      const candidate =
        candidateInput.kind === "cash"
          ? mixedRefundRequestSchema.parse(request)
          : mixedZeroReturnRequestSchema.parse(request);
      if (JSON.stringify(candidate) !== JSON.stringify(pending.request))
        throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
    }
    if (expectedKind !== (isZeroMixedReturn(pending) ? "mixed_zero_return" : "cash"))
      throw new Error("Есть незавершённый возврат. Сначала проверьте его результат.");
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    if (status.state !== "not_found") return status;
    if (pending.definiteRejected) {
      pending = { ...pending, definiteRejected: false };
      if (!writeStoredJson(mixedRefundKey(scope), pending))
        throw new Error("Не удалось сохранить повтор возврата. Операция не отправлена.");
      assertCurrent(pending, scope, active);
    }
    try {
      const completed = isZeroMixedReturn(pending)
        ? await completeMixedZeroReturn(scope.parentSaleId, pending.request, scope, reviewedQuote)
        : await completeMixedRefund(scope.parentSaleId, pending.request, scope);
      assertCurrent(pending, scope, active);
      return completed;
    } catch (error) {
      if (definitiveRejection(error)) {
        assertCurrent(pending, scope, active);
        if (!writeStoredJson(mixedRefundKey(scope), { ...pending, definiteRejected: true }))
          throw new Error("Не удалось сохранить отказ сервера. Проверьте результат возврата.", {
            cause: error,
          });
      }
      throw error;
    }
  });
}
/** A lost response is never abandoned. Recalculation follows an explicit rejection plus a fresh GET. */
export async function abandonMixedRefund(
  scope: MixedRefundScope,
): Promise<MixedReturnResult | { state: "cleared" }> {
  return exclusively(scope, async (active) => {
    const pending = loadMixedRefund(scope);
    if (!pending?.definiteRejected)
      throw new Error("Результат возврата неизвестен. Сначала проверьте эту же операцию.");
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    if (status.state !== "not_found") return status;
    if (!removeStoredValue(mixedRefundKey(scope)))
      throw new Error("Не удалось закрыть отклонённый запрос. Новую операцию не отправляйте.");
    return { state: "cleared" };
  });
}
/** Server-confirmed acknowledgment only; local storage never acts as proof of success. */
export async function acknowledgeMixedRefund(
  scope: MixedRefundScope,
  operationId: string,
): Promise<boolean> {
  return exclusively(scope, async (active) => {
    const pending = loadMixedRefund(scope);
    if (!pending || pending.request.operation_id !== operationId) return false;
    assertCurrent(pending, scope, active);
    const status = await inspectPending(pending, scope);
    assertCurrent(pending, scope, active);
    return status.state !== "not_found" && removeStoredValue(mixedRefundKey(scope));
  });
}
