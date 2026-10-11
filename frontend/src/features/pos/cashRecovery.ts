import { z } from "zod";

import { generateUuidV4 } from "./operationId";
import { removeStoredValue, writeStoredJson } from "./operationStorage";
import {
  cashConfirmationRequestSchema,
  type CashConfirmationRequest,
} from "./physicalCashCheckout";
import { physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";
import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";

const pendingSchema = z
  .object({
    version: z.literal(1),
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    createdAt: z.number().int().positive().safe(),
    request: cashConfirmationRequestSchema,
  })
  .strict();
export type PendingCash = z.infer<typeof pendingSchema>;
export type CashInput = Omit<CashConfirmationRequest, "operation_id">;
type StorageKey = (saleId: string, scope: PosStorageScope) => string;
type OperationStatus<Result> =
  | { state: "not_found" | "saved_draft" }
  | { state: "completed"; result: Result };

/** Keep channel-specific queues while sharing every durability and session barrier. */
export function createCashRecovery<Result>(channel: {
  key: StorageKey;
  oppositeKey: StorageKey;
  get: (
    request: CashConfirmationRequest,
    scope: PosStorageScope,
  ) => Promise<OperationStatus<Result>>;
  save: (request: CashConfirmationRequest, scope: PosStorageScope) => Promise<unknown>;
  complete: (request: CashConfirmationRequest, scope: PosStorageScope) => Promise<Result>;
}) {
  /** Unknown, expired or corrupt financial state must never be discarded. */
  function load(saleId: string, scope: PosStorageScope): PendingCash | null {
    let raw: string | null;
    try {
      raw = window.localStorage.getItem(channel.key(saleId, scope));
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
  function assertNoOpposite(saleId: string, scope: PosStorageScope): void {
    let raw: string | null;
    try {
      raw = window.localStorage.getItem(channel.oppositeKey(saleId, scope));
    } catch {
      throw new Error("Не удалось прочитать сохранённую оплату. Проверьте доступ к хранилищу.");
    }
    if (raw !== null)
      throw new Error("Есть незавершённая оплата другого вида. Сначала проверьте её результат.");
  }
  function assertCurrent(
    pending: PendingCash,
    scope: PosStorageScope,
    active: () => boolean,
  ): void {
    if (!active()) throw new Error("Аккаунт изменился. Откройте кассу заново.");
    assertNoOpposite(pending.request.sale_id, scope);
    if (JSON.stringify(load(pending.request.sale_id, scope)) !== JSON.stringify(pending))
      throw new Error("Сохранённая оплата изменилась. Проверьте её состояние.");
  }
  async function exclusively<T>(
    saleId: string,
    scope: PosStorageScope,
    execute: (active: () => boolean) => Promise<T>,
  ): Promise<T> {
    const active = capturePosSessionGuard(scope);
    if (!active()) throw new Error("Войдите в аккаунт кассира.");
    if (!navigator.locks?.request)
      throw new Error(
        "Для безопасной оплаты откройте кассу в приложении или современном браузере.",
      );
    // The original physical key is the lock for both channels, including queue
    // creation. Different tabs cannot create different operations for one sale.
    return navigator.locks.request(physicalCashRecoveryKey(saleId, scope), async () => {
      if (!active()) throw new Error("Аккаунт изменился. Откройте кассу заново.");
      assertNoOpposite(saleId, scope);
      return execute(active);
    });
  }
  async function run(saleId: string, scope: PosStorageScope, input?: CashInput): Promise<Result> {
    return exclusively(saleId, scope, async (active) => {
      let pending = load(saleId, scope);
      if (!pending) {
        if (!input) throw new Error("Сохранённой оплаты нет.");
        const request = cashConfirmationRequestSchema.parse({
          ...input,
          operation_id: generateUuidV4(),
        });
        if (request.sale_id !== saleId) throw new Error("Оплата относится к другому чеку.");
        pending = pendingSchema.parse({ version: 1, ...scope, createdAt: Date.now(), request });
        if (!writeStoredJson(channel.key(saleId, scope), pending))
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
      const status = await channel.get(pending.request, scope);
      assertCurrent(pending, scope, active);
      if (status.state === "completed") return status.result;
      if (status.state === "not_found") {
        await channel.save(pending.request, scope);
        assertCurrent(pending, scope, active);
      }
      await channel.complete(pending.request, scope);
      assertCurrent(pending, scope, active);
      const completed = await channel.get(pending.request, scope);
      assertCurrent(pending, scope, active);
      if (completed.state !== "completed")
        throw new Error("Результат оплаты ещё не подтверждён. Проверьте эту же операцию повторно.");
      return completed.result;
    });
  }
  /** The caller acknowledges only after recording/displaying the result. */
  async function acknowledge(
    saleId: string,
    operationId: string,
    scope: PosStorageScope,
  ): Promise<boolean> {
    return exclusively(saleId, scope, async (active) => {
      const pending = load(saleId, scope);
      if (!pending || pending.request.operation_id !== operationId) return false;
      assertCurrent(pending, scope, active);
      const status = await channel.get(pending.request, scope);
      assertCurrent(pending, scope, active);
      return status.state === "completed" && removeStoredValue(channel.key(saleId, scope));
    });
  }
  return { load, run, acknowledge };
}
