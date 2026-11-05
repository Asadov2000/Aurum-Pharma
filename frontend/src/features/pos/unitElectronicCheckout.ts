import { isAxiosError } from "axios";
import { z } from "zod";

import { api, withAuthSession } from "@/lib/api";
import { hasCashRecovery, physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";
import { clearUnitPaymentMarker, loadUnitPaymentMarker } from "./unitPaymentPreparationStorage";
import { physicalResultLineSchema } from "./physicalCashCheckout";
import { mixedLegacyResultLineSchema } from "./mixedCashCheckout";
import { capturePosSessionGuard, posStorageKey, type PosStorageScope } from "./posStorageScope";
import {
  parseSavedUnitPaymentIntent,
  parseUnitPaymentBinding,
  unitPaymentBindingAction,
  paymentCents,
  type SavedUnitPaymentIntent,
  type UnitPaymentBinding,
} from "./unitPaymentPreparation";

const uuid = z.string().uuid();
const uuid4 = uuid.regex(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const timestamp = z.string().datetime({ offset: true });
const text = (max: number) =>
  z
    .string()
    .trim()
    .min(1)
    .max(max)
    .refine((value) =>
      Array.from(value).every((char) => {
        const code = char.charCodeAt(0);
        return code >= 32 && (code < 127 || code > 159);
      }),
    );
export const evidenceSchema = z
  .object({ terminal_id: text(64), external_reference: text(128) })
  .strict();
export const voidSchema = z
  .object({
    reason: z.enum([
      "cashier_cancelled",
      "customer_cancelled",
      "terminal_declined",
      "timeout",
      "duplicate",
      "checkout_failed",
      "manager_override",
    ]),
    operator_note: z
      .string()
      .trim()
      .max(160)
      .refine(
        (value) =>
          !value.includes("@") &&
          !value.includes("://") &&
          !value.toLowerCase().includes("www.") &&
          !/[\p{Nd}\p{No}]{6}/u.test(value) &&
          Array.from(value).every((char) => char.charCodeAt(0) >= 32),
      )
      .transform((value) => value || null)
      .nullable()
      .optional(),
    terminal_id: text(64).nullable().optional(),
    external_reference: text(128).nullable().optional(),
  })
  .strict()
  .refine((v) => Boolean(v.terminal_id) === Boolean(v.external_reference));
export const attemptSchema = z
  .object({
    id: uuid4,
    tenant_id: uuid,
    sale_id: uuid,
    cashier_user_id: uuid,
    operation_id: uuid4,
    payment_method: z.enum(["card", "qr"]),
    amount: money,
    currency: z.literal("TJS"),
    status: z.enum(["pending", "requires_reconciliation", "confirmed", "consumed", "voided"]),
    terminal_id: z.string().nullable(),
    external_reference: z.string().nullable(),
    resolved_by_user_id: uuid.nullable(),
    reconciliation_started_at: timestamp.nullable(),
    evidence_required: z.boolean(),
    void_reason: z.string().nullable(),
    void_note: z.string().nullable(),
    created_at: timestamp,
    confirmed_at: timestamp.nullable(),
    consumed_at: timestamp.nullable(),
    voided_at: timestamp.nullable(),
  })
  .strict();
export type ElectronicAttempt = z.infer<typeof attemptSchema>;
const attemptIds = z.object({ card: uuid4.optional(), qr: uuid4.optional() }).strict();
export const commandSchema = z.union([
  z
    .object({
      action: z.literal("attempts"),
      payload: z.object({ attempt_ids: attemptIds }).strict(),
    })
    .strict(),
  z
    .object({
      action: z.literal("reconciliation"),
      attemptId: uuid4,
      payload: z.object({}).strict(),
    })
    .strict(),
  z.object({ action: z.literal("confirm"), attemptId: uuid4, payload: evidenceSchema }).strict(),
  z.object({ action: z.literal("void"), attemptId: uuid4, payload: voidSchema }).strict(),
  z.object({ action: z.literal("complete"), payload: z.object({}).strict() }).strict(),
]);
export type ElectronicCommand = z.infer<typeof commandSchema>;
const pendingSchema = z
  .object({
    tenantId: uuid,
    userId: uuid,
    saleId: uuid,
    operationId: uuid4,
    branchId: uuid,
    registerId: uuid,
    planHash: z.string().regex(/^[a-f0-9]{64}$/),
    command: commandSchema,
    rejection: z
      .object({ kind: z.literal("duplicate_reference"), received_at: timestamp })
      .strict()
      .optional(),
  })
  .strict();
const physicalResultSchema = z
  .object({
    schema_version: z.literal(3),
    document_kind: z.literal("physical_checkout"),
    tenant_id: uuid,
    cashier_id: uuid,
    sale_id: uuid,
    branch_id: uuid,
    register_id: uuid,
    shift_id: uuid,
    operation_id: uuid4,
    event_id: uuid4,
    stock_command_id: uuid4,
    created_at: timestamp,
    completed_at: timestamp,
    receipt_number: text(100),
    receipt_sequence: z.number().int().positive(),
    is_test: z.literal(false),
    lines: z.array(physicalResultLineSchema).min(1).max(200),
    payments: z
      .array(
        z
          .object({
            id: uuid4,
            method: z.enum(["cash", "card", "qr"]),
            amount: money,
            currency: z.literal("TJS"),
          })
          .strict(),
      )
      .min(1)
      .max(3),
    total_amount: money,
    currency: z.literal("TJS"),
  })
  .strict();
const mixedResultSchema = physicalResultSchema.extend({
  schema_version: z.literal(4),
  document_kind: z.literal("mixed_checkout"),
  lines: z
    .array(z.union([mixedLegacyResultLineSchema, physicalResultLineSchema]))
    .min(2)
    .max(200),
});
const resultSchema = z.union([physicalResultSchema, mixedResultSchema]);
export type ElectronicCheckoutResult = z.infer<typeof resultSchema>;
export interface ElectronicCheckoutContext {
  intent: SavedUnitPaymentIntent;
  binding: UnitPaymentBinding;
  scope: PosStorageScope;
  branchId: string;
  registerId: string;
  isActive: () => boolean;
}
const failure = () =>
  new Error("Данные электронной оплаты не соответствуют сохранённому плану. Касса заблокирована.");
type ElectronicOwnerContext = Omit<ElectronicCheckoutContext, "binding">;
function owner(c: ElectronicOwnerContext) {
  const q = c.intent.plan.quote;
  return {
    tenantId: c.scope.tenantId,
    userId: c.scope.userId,
    saleId: q.sale_id,
    operationId: c.intent.operation_id,
    branchId: q.branch_id,
    registerId: q.register_id,
    planHash: c.intent.plan.plan_hash,
  };
}
const key = (c: ElectronicOwnerContext) =>
  posStorageKey("pos:unitElectronicCommand:", c.intent.plan.request.sale_id, c.scope);
function pendingRecord(c: ElectronicOwnerContext): z.infer<typeof pendingSchema> | null {
  const raw = localStorage.getItem(key(c));
  if (raw === null) return null;
  const saved = pendingSchema.parse(JSON.parse(raw));
  if (Object.entries(owner(c)).some(([k, v]) => saved[k as keyof typeof saved] !== v))
    throw failure();
  return saved;
}
export function loadElectronicCommand(c: ElectronicOwnerContext): ElectronicCommand | null {
  return pendingRecord(c)?.command ?? null;
}
export function hasRejectedElectronicCommand(c: ElectronicCheckoutContext): boolean {
  return pendingRecord(c)?.rejection?.kind === "duplicate_reference";
}
class RejectedElectronicCommand extends Error {
  constructor() {
    super(
      "Сервер отклонил занятый внешний номер. Проверьте сохранённые факты и явно исправьте реквизиты на той же попытке.",
    );
  }
}

function storeCommand(c: ElectronicCheckoutContext, command: ElectronicCommand): void {
  const value = pendingSchema.parse({ ...owner(c), command });
  if (hasRejectedElectronicCommand(c)) throw new RejectedElectronicCommand();
  const previous = loadElectronicCommand(c);
  if (previous && JSON.stringify(previous) !== JSON.stringify(command)) throw failure();
  const raw = JSON.stringify(value);
  localStorage.setItem(key(c), raw);
  if (localStorage.getItem(key(c)) !== raw) throw failure();
}
function clearElectronicCommand(c: ElectronicCheckoutContext): void {
  if (!capturePosSessionGuard(c.scope)() || !c.isActive()) throw failure();
  loadElectronicCommand(c);
  localStorage.removeItem(key(c));
  if (localStorage.getItem(key(c)) !== null) throw failure();
}
function validateIds(c: ElectronicCheckoutContext, ids: z.infer<typeof attemptIds>): void {
  const parts = c.intent.plan.request.payments.filter((p) => p.method !== "cash");
  if (
    Object.keys(ids).length !== parts.length ||
    parts.some((p) => !ids[p.method as "card" | "qr"]) ||
    new Set(Object.values(ids)).size !== parts.length ||
    Object.values(ids).includes(c.intent.operation_id)
  )
    throw failure();
}
export function parseElectronicAttempts(
  data: unknown,
  c: ElectronicCheckoutContext,
): ElectronicAttempt[] {
  const rows = z.array(attemptSchema).min(1).max(2).parse(data);
  const ids: z.infer<typeof attemptIds> = {};
  for (const row of rows) {
    const part = c.intent.plan.request.payments.find((p) => p.method === row.payment_method);
    if (
      ids[row.payment_method] ||
      row.tenant_id !== c.scope.tenantId ||
      row.cashier_user_id !== c.scope.userId ||
      row.sale_id !== c.intent.plan.request.sale_id ||
      row.operation_id !== row.id ||
      !row.evidence_required ||
      !part ||
      row.amount !== part.amount ||
      ((row.status === "confirmed" || row.status === "consumed") &&
        (!row.confirmed_at ||
          !row.reconciliation_started_at ||
          !evidenceSchema.safeParse({
            terminal_id: row.terminal_id,
            external_reference: row.external_reference,
          }).success)) ||
      (row.status === "consumed" && !row.consumed_at)
    )
      throw failure();
    ids[row.payment_method] = row.id;
  }
  validateIds(c, ids);
  const pending = loadElectronicCommand(c);
  if (
    pending?.action === "attempts" &&
    JSON.stringify(attemptIds.parse(ids)) !==
      JSON.stringify(attemptIds.parse(pending.payload.attempt_ids))
  )
    throw failure();
  return rows;
}
export function parseElectronicResult(
  data: unknown,
  c: ElectronicCheckoutContext,
  attempts: ElectronicAttempt[],
): ElectronicCheckoutResult {
  const r = resultSchema.parse(data),
    q = c.intent.plan.quote;
  const ids = [r.operation_id, r.event_id, r.stock_command_id];
  if (attempts.length === 0 || attempts.some((a) => a.status !== "consumed")) throw failure();
  if (
    r.document_kind !== (isMixedPlan(c) ? "mixed_checkout" : "physical_checkout") ||
    ids.some((id) => id !== c.intent.operation_id) ||
    r.tenant_id !== c.scope.tenantId ||
    r.cashier_id !== c.scope.userId ||
    r.sale_id !== q.sale_id ||
    r.branch_id !== q.branch_id ||
    r.register_id !== q.register_id ||
    r.shift_id !== q.shift_id ||
    r.total_amount !== q.total_amount ||
    Date.parse(r.completed_at) < Date.parse(r.created_at) ||
    r.lines.length !== q.lines.length ||
    new Set(r.lines.map((l) => l.id)).size !== r.lines.length ||
    r.lines.some((line, i) => {
      const original = Object.fromEntries(Object.entries(line).filter(([key]) => key !== "name"));
      return JSON.stringify(original) !== JSON.stringify(q.lines[i]);
    }) ||
    r.payments.length !== c.intent.plan.request.payments.length ||
    new Set(r.payments.map((p) => p.id)).size !== r.payments.length ||
    new Set(r.payments.map((p) => p.method)).size !== r.payments.length ||
    r.payments.some(
      (p) =>
        !c.intent.plan.request.payments.some(
          (part) => part.method === p.method && part.amount === p.amount,
        ) ||
        p.id !==
          (p.method === "cash"
            ? c.intent.operation_id
            : attempts.find((a) => a.payment_method === p.method)?.id),
    ) ||
    r.payments.reduce((sum, p) => sum + paymentCents(p.amount), 0n) !== paymentCents(r.total_amount)
  )
    throw failure();
  return r;
}
async function checkedContext(c: ElectronicCheckoutContext): Promise<() => void> {
  const guard = capturePosSessionGuard(c.scope);
  const check = () => {
    if (
      !guard() ||
      !c.isActive() ||
      c.intent.plan.quote.branch_id !== c.branchId ||
      c.intent.plan.quote.register_id !== c.registerId
    )
      throw failure();
  };
  check();
  const intent = await parseSavedUnitPaymentIntent(
    c.intent,
    c.intent.plan.request.sale_id,
    c.scope,
    c.intent.operation_id,
  );
  check();
  if (parseUnitPaymentBinding(c.binding, intent).state !== "bound") throw failure();
  return check;
}
function isMixedPlan(c: ElectronicOwnerContext): boolean {
  return c.intent.plan.quote.lines.some((line) => line.inventory_kind === "legacy_pack");
}
function url(c: ElectronicCheckoutContext): string {
  const prefix = `/sales/${uuid.parse(c.intent.plan.request.sale_id)}/unit-payment-plan/intents/${uuid4.parse(c.intent.operation_id)}`;
  return isMixedPlan(c) ? `${prefix}/mixed-checkout` : prefix;
}
async function get(c: ElectronicCheckoutContext, suffix: string): Promise<unknown | null> {
  try {
    return (await api.get<unknown>(url(c) + suffix, withAuthSession({ timeout: 8_000 }, true)))
      .data;
  } catch (error) {
    if (isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}
function commandProven(
  command: ElectronicCommand,
  attempts: ElectronicAttempt[],
  result: ElectronicCheckoutResult | null,
): boolean {
  if (command.action === "complete") return result !== null;
  if (command.action === "attempts")
    return (
      attempts.length === Object.keys(command.payload.attempt_ids).length &&
      attempts.every((row) => command.payload.attempt_ids[row.payment_method] === row.id)
    );
  const row = attempts.find((a) => a.id === command.attemptId);
  if (!row) return false;
  if (command.action === "reconciliation")
    return (
      row.reconciliation_started_at !== null &&
      ["requires_reconciliation", "confirmed", "consumed"].includes(row.status)
    );
  if (command.action === "confirm")
    return (
      ["confirmed", "consumed"].includes(row.status) &&
      row.terminal_id === command.payload.terminal_id &&
      row.external_reference === command.payload.external_reference
    );
  return (
    row.status === "voided" &&
    row.voided_at !== null &&
    row.void_reason === command.payload.reason &&
    (row.void_note ?? null) === (command.payload.operator_note ?? null) &&
    (row.terminal_id ?? null) === (command.payload.terminal_id ?? null) &&
    (row.external_reference ?? null) === (command.payload.external_reference ?? null)
  );
}
function clearProvenCommand(c: ElectronicCheckoutContext, command: ElectronicCommand): void {
  if (JSON.stringify(loadElectronicCommand(c)) !== JSON.stringify(command)) throw failure();
  clearElectronicCommand(c);
}

/** Read only: a missing response never prepares attempts or submits money commands. */
async function recoverUnlocked(
  c: ElectronicCheckoutContext,
): Promise<{ attempts: ElectronicAttempt[]; result: ElectronicCheckoutResult | null }> {
  const check = await checkedContext(c);
  check();
  const saved = await get(c, "/checkout");
  check();
  const data = await get(c, "/attempts");
  check();
  const attempts = data === null ? [] : parseElectronicAttempts(data, c);
  const result = saved === null ? null : parseElectronicResult(saved, c, attempts);
  const pending = loadElectronicCommand(c);
  if (pending && commandProven(pending, attempts, result)) {
    check();
    clearProvenCommand(c, pending);
  }
  return { attempts, result };
}
async function executeUnlocked(
  c: ElectronicCheckoutContext,
  input: ElectronicCommand,
): Promise<void> {
  const check = await checkedContext(c);
  check();
  const command = commandSchema.parse(input);
  if (hasCashRecovery(c.intent.plan.request.sale_id, c.scope)) throw failure();
  if (command.action === "attempts") {
    validateIds(c, command.payload.attempt_ids);
    const existing = await get(c, "/attempts");
    check();
    if (existing !== null) {
      const rows = parseElectronicAttempts(existing, c);
      if (!commandProven(command, rows, null)) throw failure();
    }
  }
  if (loadUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope) !== c.intent.operation_id)
    throw failure();
  storeCommand(c, command);
  check();
  const suffix =
    "attemptId" in command
      ? `/attempts/${command.attemptId}/${command.action}`
      : `/${command.action}`;
  let data: unknown;
  try {
    data = (
      await api.post<unknown>(
        url(c) + suffix,
        command.payload,
        withAuthSession({ timeout: 8_000 }, true),
      )
    ).data;
  } catch (error) {
    check();
    const definite =
      command.action === "confirm" &&
      isAxiosError<unknown>(error) &&
      error.response?.status === 409 &&
      z
        .object({
          error: z.object({
            code: z.literal("conflict"),
            message: z.literal("Terminal payment reference was already used"),
          }),
        })
        .safeParse(error.response.data).success;
    if (definite) {
      const saved = pendingRecord(c);
      if (!saved || JSON.stringify(saved.command) !== JSON.stringify(command)) throw failure();
      const rejected = pendingSchema.parse({
        ...saved,
        rejection: { kind: "duplicate_reference", received_at: new Date().toISOString() },
      });
      const raw = JSON.stringify(rejected);
      localStorage.setItem(key(c), raw);
      if (localStorage.getItem(key(c)) !== raw) throw failure();
      throw new RejectedElectronicCommand();
    }
    throw error;
  }
  check();
  if (command.action === "attempts") {
    parseElectronicAttempts(data, c);
    check();
    clearProvenCommand(c, command);
    return;
  }
  const state = await recoverUnlocked(c);
  check();
  if (!commandProven(command, state.attempts, state.result)) throw failure();
  if (command.action === "complete") parseElectronicResult(data, c, state.attempts);
  else {
    const row = attemptSchema.parse(data),
      matched = state.attempts.find((a) => a.id === command.attemptId);
    if (
      !matched ||
      row.id !== matched.id ||
      row.operation_id !== matched.operation_id ||
      row.tenant_id !== matched.tenant_id ||
      row.cashier_user_id !== matched.cashier_user_id ||
      row.sale_id !== matched.sale_id ||
      row.payment_method !== matched.payment_method ||
      row.amount !== matched.amount
    )
      throw failure();
  }
  check();
}

async function exclusively<T>(
  c: ElectronicOwnerContext,
  work: (check: () => void) => Promise<T>,
): Promise<T> {
  const guard = capturePosSessionGuard(c.scope),
    snapshot = owner(c);
  const check = () => {
    if (
      !guard() ||
      !c.isActive() ||
      JSON.stringify(owner(c)) !== JSON.stringify(snapshot) ||
      c.branchId !== snapshot.branchId ||
      c.registerId !== snapshot.registerId
    )
      throw failure();
  };
  check();
  if (!navigator.locks?.request)
    throw new Error("Для безопасной оплаты нужен браузер с межвкладочной блокировкой.");
  return navigator.locks.request(physicalCashRecoveryKey(snapshot.saleId, c.scope), async () => {
    check();
    const value = await work(check);
    check();
    return value;
  });
}
export async function recoverElectronicCheckout(
  c: ElectronicCheckoutContext,
): Promise<{ attempts: ElectronicAttempt[]; result: ElectronicCheckoutResult | null }> {
  return exclusively(c, async (check) => {
    check();
    const state = await recoverUnlocked(c);
    check();
    return state;
  });
}
export async function executeElectronicCommand(
  c: ElectronicCheckoutContext,
  input: ElectronicCommand,
): Promise<void> {
  return exclusively(c, async (check) => {
    check();
    await executeUnlocked(c, input);
    check();
  });
}
/** Only a persisted, recognized rollback plus fresh scoped non-applied facts allows explicit correction. */
export async function releaseRejectedElectronicCommand(
  c: ElectronicCheckoutContext,
): Promise<void> {
  return exclusively(c, async (check) => {
    const rejected = pendingRecord(c);
    if (!rejected?.rejection || rejected.command.action !== "confirm") throw failure();
    const command = rejected.command;
    const state = await recoverUnlocked(c);
    check();
    if (loadElectronicCommand(c) === null) return;
    const row = state.attempts.find((a) => a.id === command.attemptId);
    if (
      state.result ||
      !row ||
      row.status !== "requires_reconciliation" ||
      row.terminal_id !== null ||
      row.external_reference !== null ||
      row.confirmed_at !== null ||
      row.consumed_at !== null ||
      row.voided_at !== null ||
      row.reconciliation_started_at === null
    )
      throw failure();
    check();
    clearProvenCommand(c, command);
  });
}
/** Receipt acceptance and both marker cleanups share the same lock; no nested recovery lock. */
export async function acknowledgeElectronicCheckout(
  c: ElectronicCheckoutContext,
  result: ElectronicCheckoutResult,
  accept: (saved: ElectronicCheckoutResult) => Promise<boolean>,
): Promise<boolean> {
  return exclusively(c, async (check) => {
    if (hasCashRecovery(c.intent.plan.request.sale_id, c.scope)) throw failure();
    const state = await recoverUnlocked(c);
    check();
    if (
      !state.result ||
      JSON.stringify(state.result) !==
        JSON.stringify(parseElectronicResult(result, c, state.attempts)) ||
      loadUnitPaymentMarker(result.sale_id, c.scope) !== result.operation_id
    )
      throw failure();
    if (!(await accept(state.result))) {
      check();
      return false;
    }
    check();
    if (loadUnitPaymentMarker(result.sale_id, c.scope) !== result.operation_id) throw failure();
    clearElectronicCommand(c);
    clearUnitPaymentMarker(result.sale_id, c.scope, result.operation_id);
    return true;
  });
}

/** Cancellation must not race a different tab creating a financial command. */
export async function cancelElectronicPreparation(
  c: ElectronicOwnerContext,
  cancel: () => Promise<UnitPaymentBinding | null>,
): Promise<void> {
  return exclusively(c, async (check) => {
    if (hasCashRecovery(c.intent.plan.request.sale_id, c.scope)) throw failure();
    const actual = await unitPaymentBindingAction(c.intent, "binding", c.scope);
    check();
    if (actual?.state === "bound") {
      const state = await recoverUnlocked({ ...c, binding: actual });
      check();
      if (
        state.result ||
        loadElectronicCommand(c) !== null ||
        state.attempts.some((a) => a.status !== "voided")
      )
        throw failure();
    } else if (loadElectronicCommand(c) !== null) throw failure();
    const cancelled = actual?.state === "cancelled" ? actual : await cancel();
    check();
    if (!cancelled || parseUnitPaymentBinding(cancelled, c.intent).state !== "cancelled")
      throw failure();
    clearUnitPaymentMarker(c.intent.plan.request.sale_id, c.scope, c.intent.operation_id);
  });
}
