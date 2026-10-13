import { z } from "zod";

import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";

import {
  completePhysicalDisposition,
  getPhysicalDispositionStatus,
  physicalDispositionInputSchema,
  physicalDispositionRequestSchema,
  physicalDispositionReviewedTargetSchema,
  physicalDispositionTarget,
  type PhysicalDispositionInput,
  type PhysicalDispositionReviewedTarget,
  type PhysicalDispositionScope,
  type PhysicalDispositionStatus,
  type ResolvedPhysicalDisposition,
} from "./physicalDisposition";

export type { PhysicalDispositionScope } from "./physicalDisposition";
const identitySchema = z.object({ tenantId: z.string().uuid(), userId: z.string().uuid() });
const pendingSchema = identitySchema
  .extend({
    version: z.literal(1),
    branchId: z.string().uuid(),
    request: physicalDispositionRequestSchema,
    reviewedTarget: physicalDispositionReviewedTargetSchema,
  })
  .strict()
  .refine(
    (value) =>
      JSON.stringify(value.request.target) ===
      JSON.stringify(physicalDispositionTarget(value.reviewedTarget)),
  );
export type PendingPhysicalDisposition = z.infer<typeof pendingSchema>;
export function physicalDispositionKey(scope: PosStorageScope): string {
  identitySchema.parse(scope);
  return `customerReturns:physicalDisposition:v1:${encodeURIComponent(JSON.stringify([scope.tenantId, scope.userId]))}`;
}
export function loadPhysicalDisposition(scope: PosStorageScope): PendingPhysicalDisposition | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(physicalDispositionKey(scope));
  } catch {
    throw new Error("Не удалось прочитать сохранённое решение. Проверьте хранилище.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (pending.tenantId !== scope.tenantId || pending.userId !== scope.userId)
      throw new Error("Scope mismatch");
    return pending;
  } catch {
    throw new Error("Сохранённое решение повреждено. Обратитесь к администратору.");
  }
}
function assertCurrent(
  pending: PendingPhysicalDisposition,
  scope: PosStorageScope,
  active: () => boolean,
): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте решение заново.");
  if (JSON.stringify(loadPhysicalDisposition(scope)) !== JSON.stringify(pending))
    throw new Error("Сохранённое решение изменилось. Проверьте его статус заново.");
}
async function exclusively<T>(
  scope: PhysicalDispositionScope,
  execute: (active: () => boolean) => Promise<T>,
): Promise<T> {
  z.string().uuid().parse(scope.branchId);
  const key = physicalDispositionKey(scope);
  const active = capturePosSessionGuard(scope);
  if (!active()) throw new Error("Войдите в аккаунт сотрудника.");
  if (!navigator.locks?.request)
    throw new Error("Для безопасного решения используйте приложение или современный браузер.");
  return navigator.locks.request(key, async () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте решение заново.");
    return execute(active);
  });
}
function requireBranch(pending: PendingPhysicalDisposition, scope: PhysicalDispositionScope): void {
  if (pending.branchId !== scope.branchId)
    throw new Error("Есть незавершённое решение в другой точке. Сначала проверьте его статус.");
}
/** Opening a panel only checks status. Stored data can never assert server success. */
export async function inspectPhysicalDisposition(
  scope: PhysicalDispositionScope,
): Promise<PhysicalDispositionStatus | null> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalDisposition(scope);
    if (!pending) return null;
    requireBranch(pending, scope);
    assertCurrent(pending, scope, active);
    const status = await getPhysicalDispositionStatus(
      pending.request,
      pending.reviewedTarget,
      scope,
    );
    assertCurrent(pending, scope, active);
    return status;
  });
}
/** Explicit confirmation/retry; one durable request blocks every other row and branch. */
export async function runPhysicalDisposition(
  scope: PhysicalDispositionScope,
  input?: PhysicalDispositionInput,
  reviewedTarget?: PhysicalDispositionReviewedTarget,
): Promise<ResolvedPhysicalDisposition> {
  return exclusively(scope, async (active) => {
    let pending = loadPhysicalDisposition(scope);
    if (pending) {
      requireBranch(pending, scope);
      if (input) {
        const candidate = physicalDispositionRequestSchema.parse({
          ...physicalDispositionInputSchema.parse(input),
          decision_operation_id: pending.request.decision_operation_id,
        });
        if (JSON.stringify(candidate) !== JSON.stringify(pending.request))
          throw new Error("Есть незавершённое решение. Сначала проверьте его статус.");
      }
      if (
        reviewedTarget &&
        JSON.stringify(physicalDispositionReviewedTargetSchema.parse(reviewedTarget)) !==
          JSON.stringify(pending.reviewedTarget)
      )
        throw new Error("Количество сохранённого решения отличается. Проверьте его статус.");
    } else {
      if (!input || !reviewedTarget) throw new Error("Сначала подтвердите всю строку возврата.");
      pending = pendingSchema.parse({
        version: 1,
        tenantId: scope.tenantId,
        userId: scope.userId,
        branchId: scope.branchId,
        request: physicalDispositionRequestSchema.parse({
          ...physicalDispositionInputSchema.parse(input),
          decision_operation_id: generateUuidV4(),
        }),
        reviewedTarget,
      });
      if (!writeStoredJson(physicalDispositionKey(scope), pending))
        throw new Error("Не удалось сохранить решение. Запрос не отправлен.");
    }
    assertCurrent(pending, scope, active);
    const status = await getPhysicalDispositionStatus(
      pending.request,
      pending.reviewedTarget,
      scope,
    );
    assertCurrent(pending, scope, active);
    if (status.state === "resolved") return status;
    const result = await completePhysicalDisposition(
      pending.request,
      pending.reviewedTarget,
      scope,
    );
    assertCurrent(pending, scope, active);
    return result;
  });
}
/** No abandonment/reset: only a freshly confirmed immutable decision releases the barrier. */
export async function acknowledgePhysicalDisposition(
  scope: PhysicalDispositionScope,
  result: ResolvedPhysicalDisposition,
): Promise<boolean> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalDisposition(scope);
    if (!pending) return false;
    requireBranch(pending, scope);
    assertCurrent(pending, scope, active);
    const status = await getPhysicalDispositionStatus(
      pending.request,
      pending.reviewedTarget,
      scope,
    );
    assertCurrent(pending, scope, active);
    if (status.state !== "resolved" || JSON.stringify(status) !== JSON.stringify(result))
      return false;
    if (!removeStoredValue(physicalDispositionKey(scope)))
      throw new Error("Не удалось удалить подтверждённое решение. Проверьте хранилище.");
    return true;
  });
}
