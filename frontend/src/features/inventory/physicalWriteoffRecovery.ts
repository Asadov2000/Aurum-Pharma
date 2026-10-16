import { isAxiosError } from "axios";
import { z } from "zod";

import { generateUuidV4 } from "@/features/pos/operationId";
import { removeStoredValue, writeStoredJson } from "@/features/pos/operationStorage";
import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";

import {
  completePhysicalWriteoff,
  getPhysicalWriteoffStatus,
  physicalWriteoffInputSchema,
  physicalWriteoffInteger,
  physicalWriteoffRequestSchema,
  type PhysicalWriteoffInput,
  type PhysicalWriteoffResult,
  type PhysicalWriteoffScope,
} from "./physicalWriteoff";

export type { PhysicalWriteoffScope } from "./physicalWriteoff";
const identity = z.object({ tenantId: z.string().uuid(), userId: z.string().uuid() });
const pendingSchema = identity
  .extend({
    version: z.literal(1),
    branchId: z.string().uuid(),
    request: physicalWriteoffRequestSchema,
    expectedBaseUnits: physicalWriteoffInteger.refine(
      (value) => BigInt(value) > 0n && BigInt(value) <= 99999999999n,
    ),
  })
  .strict()
  .refine((value) => value.branchId === value.request.branch_id);
export type PendingPhysicalWriteoff = z.infer<typeof pendingSchema>;

export function physicalWriteoffKey(scope: PosStorageScope): string {
  identity.parse(scope);
  return `inventory:physicalWriteoff:v1:${encodeURIComponent(JSON.stringify([scope.tenantId, scope.userId]))}`;
}
export function loadPhysicalWriteoff(scope: PosStorageScope): PendingPhysicalWriteoff | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(physicalWriteoffKey(scope));
  } catch {
    throw new Error("Не удалось прочитать сохранённое списание. Новые операции недоступны.");
  }
  if (raw === null) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (pending.tenantId !== scope.tenantId || pending.userId !== scope.userId)
      throw new Error("Scope mismatch");
    return pending;
  } catch {
    throw new Error("Сохранённое списание повреждено. Обратитесь к администратору.");
  }
}
async function exclusively<T>(
  scope: PhysicalWriteoffScope,
  execute: (active: () => boolean) => Promise<T>,
): Promise<T> {
  z.string().uuid().parse(scope.branchId);
  const key = physicalWriteoffKey(scope);
  const active = capturePosSessionGuard(scope);
  if (!active()) throw new Error("Войдите в аккаунт сотрудника.");
  if (!navigator.locks?.request)
    throw new Error("Для безопасного списания используйте приложение или современный браузер.");
  return navigator.locks.request(key, async () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте списание заново.");
    return execute(active);
  });
}
function assertCurrent(
  pending: PendingPhysicalWriteoff,
  scope: PhysicalWriteoffScope,
  active: () => boolean,
): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте списание заново.");
  if (pending.branchId !== scope.branchId)
    throw new Error("Есть незавершённое списание в другой точке. Сначала проверьте его статус.");
  if (JSON.stringify(loadPhysicalWriteoff(scope)) !== JSON.stringify(pending))
    throw new Error("Сохранённое списание изменилось. Проверьте его статус заново.");
}
/** Opening the workspace only observes; even 404 cannot authorize a new command. */
export async function inspectPhysicalWriteoff(
  scope: PhysicalWriteoffScope,
): Promise<PhysicalWriteoffResult | null> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalWriteoff(scope);
    if (!pending) return null;
    assertCurrent(pending, scope, active);
    const result = await getPhysicalWriteoffStatus(
      scope,
      pending.request,
      pending.expectedBaseUnits,
    );
    assertCurrent(pending, scope, active);
    return result;
  });
}
/** Explicit confirmation/retry always uses the same immutable persisted request. */
export async function runPhysicalWriteoff(
  scope: PhysicalWriteoffScope,
  input?: PhysicalWriteoffInput,
  expectedBaseUnits?: string,
): Promise<PhysicalWriteoffResult> {
  return exclusively(scope, async (active) => {
    let pending = loadPhysicalWriteoff(scope);
    const firstSubmission = pending === null;
    if (pending) {
      assertCurrent(pending, scope, active);
      if (
        input &&
        JSON.stringify(
          physicalWriteoffRequestSchema.parse({
            ...physicalWriteoffInputSchema.parse(input),
            operation_id: pending.request.operation_id,
          }),
        ) !== JSON.stringify(pending.request)
      )
        throw new Error("Есть незавершённое списание. Сначала проверьте его статус.");
      if (expectedBaseUnits !== undefined && expectedBaseUnits !== pending.expectedBaseUnits)
        throw new Error("Количество отличается от сохранённого списания.");
    } else {
      if (!input || expectedBaseUnits === undefined)
        throw new Error("Сначала подтвердите выбранные остатки.");
      pending = pendingSchema.parse({
        version: 1,
        tenantId: scope.tenantId,
        userId: scope.userId,
        branchId: scope.branchId,
        request: { ...physicalWriteoffInputSchema.parse(input), operation_id: generateUuidV4() },
        expectedBaseUnits,
      });
      if (!writeStoredJson(physicalWriteoffKey(scope), pending))
        throw new Error("Не удалось сохранить списание. Запрос не отправлен.");
    }
    assertCurrent(pending, scope, active);
    const existing = await getPhysicalWriteoffStatus(
      scope,
      pending.request,
      pending.expectedBaseUnits,
    );
    assertCurrent(pending, scope, active);
    if (existing) return existing;
    try {
      const result = await completePhysicalWriteoff(
        scope,
        pending.request,
        pending.expectedBaseUnits,
      );
      assertCurrent(pending, scope, active);
      return result;
    } catch (error) {
      // get_db rolls back domain failures before emitting the response. This
      // proves rejection only for our first submission: an earlier uncertain
      // request may still be running when a retry fails an early policy check.
      const rejection = z.object({
        error: z.object({ code: z.enum(["business_rule_violation", "validation_error"]) }),
      });
      if (
        firstSubmission &&
        isAxiosError(error) &&
        error.response?.status === 422 &&
        rejection.safeParse(error.response.data).success
      ) {
        assertCurrent(pending, scope, active);
        if (!removeStoredValue(physicalWriteoffKey(scope)))
          throw new Error("Не удалось убрать отклонённое списание. Проверьте хранилище.", {
            cause: error,
          });
      }
      throw error;
    }
  });
}
/** Only a fresh server-confirmed result releases the durable recovery barrier. */
export async function acknowledgePhysicalWriteoff(scope: PhysicalWriteoffScope): Promise<void> {
  return exclusively(scope, async (active) => {
    const pending = loadPhysicalWriteoff(scope);
    if (!pending) return;
    assertCurrent(pending, scope, active);
    const result = await getPhysicalWriteoffStatus(
      scope,
      pending.request,
      pending.expectedBaseUnits,
    );
    assertCurrent(pending, scope, active);
    if (!result) throw new Error("Результат списания ещё не подтверждён.");
    if (!removeStoredValue(physicalWriteoffKey(scope)))
      throw new Error("Не удалось убрать подтверждённое списание. Проверьте хранилище.");
  });
}
