import { isAxiosError } from "axios";
import { z } from "zod";

import { api, withAuthSession } from "@/lib/api";

import { parseMixedCashQuote } from "./mixedCashCheckout";
import { parsePhysicalCashQuote } from "./physicalCashCheckout";
import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";

const uuid = z.string().uuid();
const operationId = uuid.regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
);
const hash = z.string().regex(/^[0-9a-f]{64}$/);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const timestamp = z.string().datetime({ offset: true });
export const paymentCents = (value: string): bigint => BigInt(money.parse(value).replace(".", ""));
export function normalizePaymentMoney(value: string): string {
  const valid = z
    .string()
    .trim()
    .regex(/^\d{1,12}([.,]\d{1,2})?$/)
    .parse(value);
  const [whole = "0", fraction = ""] = valid.replace(",", ".").split(".");
  return money.parse(`${BigInt(whole)}.${fraction.padEnd(2, "0")}`);
}
const positive = money.refine((value) => paymentCents(value) > 0n);
export const unitPaymentRequestSchema = z
  .object({
    sale_id: uuid,
    expected_revision_hash: hash,
    expected_total_amount: positive,
    currency: z.literal("TJS"),
    payments: z
      .array(z.object({ method: z.enum(["cash", "card", "qr"]), amount: positive }).strict())
      .min(1)
      .max(3),
    cash_tendered: money.nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    const methods = value.payments.map((part) => part.method);
    const cash = value.payments.find((part) => part.method === "cash");
    if (
      new Set(methods).size !== methods.length ||
      !methods.some((method) => method !== "cash") ||
      value.payments.reduce((sum, part) => sum + paymentCents(part.amount), 0n) !==
        paymentCents(value.expected_total_amount) ||
      (cash
        ? value.cash_tendered === null ||
          paymentCents(value.cash_tendered) < paymentCents(cash.amount)
        : value.cash_tendered !== null)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Суммы способов оплаты должны точно совпадать с итогом; проверьте наличные и сдачу.",
      });
    }
  });
export type UnitPaymentRequest = z.infer<typeof unitPaymentRequestSchema>;
const planSchema = z
  .object({
    state: z.literal("validated_draft"),
    request: unitPaymentRequestSchema,
    quote: z.unknown(),
    change_amount: money,
    plan_hash: hash,
  })
  .strict();
const savedSchema = z
  .object({
    state: z.literal("saved_draft"),
    operation_id: operationId,
    saved_at: timestamp,
    plan: z.unknown(),
  })
  .strict();
const bindingSchema = z
  .object({
    state: z.enum(["bound", "cancelled"]),
    operation_id: operationId,
    sale_id: uuid,
    plan_hash: hash,
    bound_at: timestamp.nullable(),
    cancelled_at: timestamp.nullable(),
  })
  .strict()
  .refine((value) =>
    value.state === "bound"
      ? value.bound_at !== null && value.cancelled_at === null
      : value.cancelled_at !== null &&
        (value.bound_at === null || Date.parse(value.cancelled_at) >= Date.parse(value.bound_at)),
  );
export type UnitPaymentBinding = z.infer<typeof bindingSchema>;

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
async function digest(value: unknown): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(value)));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
function assertSession(guard: () => boolean): void {
  if (!guard()) throw new Error("Сессия изменилась. Откройте кассу заново.");
}
function path(saleId: string): string {
  return `/sales/${uuid.parse(saleId)}/unit-payment-plan`;
}
async function parseQuote(data: unknown, saleId: string, scope: PosStorageScope) {
  const envelope = z
    .object({
      quote: z.object({
        branch_id: uuid,
        register_id: uuid,
        lines: z.array(z.object({ inventory_kind: z.string() })),
      }),
      revision_hash: hash,
    })
    .parse(data);
  const parser = envelope.quote.lines.some((line) => line.inventory_kind === "legacy_pack")
    ? parseMixedCashQuote
    : parsePhysicalCashQuote;
  return parser(data, saleId, envelope.quote.branch_id, envelope.quote.register_id, scope);
}
export async function getUnitPaymentQuote(saleId: string, scope: PosStorageScope) {
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.get<unknown>(
    `/sales/${uuid.parse(saleId)}/physical-cart/quote`,
    withAuthSession({ timeout: 8_000 }, true),
  );
  const quote = await parseQuote(data, saleId, scope);
  assertSession(guard);
  return quote;
}
export async function parseUnitPaymentPlan(data: unknown, saleId: string, scope: PosStorageScope) {
  const plan = planSchema.parse(data);
  if (plan.request.sale_id !== saleId) throw new Error("План относится к другой продаже.");
  const { quote } = await parseQuote(
    { quote: plan.quote, revision_hash: plan.request.expected_revision_hash },
    saleId,
    scope,
  );
  const cash = plan.request.payments.find((part) => part.method === "cash");
  if (
    quote.total_amount !== plan.request.expected_total_amount ||
    paymentCents(plan.change_amount) !==
      (plan.request.cash_tendered === null ? 0n : paymentCents(plan.request.cash_tendered)) -
        (cash ? paymentCents(cash.amount) : 0n)
  )
    throw new Error("Суммы плана не совпадают с расчётом.");
  const expected = await digest({
    kind: "unit_payment_plan_v1",
    tenant_id: quote.tenant_id,
    branch_id: quote.branch_id,
    cashier_id: quote.cashier_id,
    sale_id: quote.sale_id,
    revision_hash: plan.request.expected_revision_hash,
    currency: "TJS",
    payments: [...plan.request.payments].sort((a, b) => a.method.localeCompare(b.method)),
    cash_tendered: plan.request.cash_tendered,
  });
  if (expected !== plan.plan_hash) throw new Error("Не удалось проверить целостность плана.");
  return { ...plan, quote };
}
export type UnitPaymentPlan = Awaited<ReturnType<typeof parseUnitPaymentPlan>>;
export async function parseSavedUnitPaymentIntent(
  data: unknown,
  saleId: string,
  scope: PosStorageScope,
  id?: string,
) {
  const saved = savedSchema.parse(data);
  if (id && saved.operation_id !== id) throw new Error("Ответ относится к другой операции.");
  return { ...saved, plan: await parseUnitPaymentPlan(saved.plan, saleId, scope) };
}
export type SavedUnitPaymentIntent = Awaited<ReturnType<typeof parseSavedUnitPaymentIntent>>;
export function parseUnitPaymentBinding(
  data: unknown,
  intent: SavedUnitPaymentIntent,
): UnitPaymentBinding {
  const binding = bindingSchema.parse(data);
  if (
    binding.operation_id !== intent.operation_id ||
    binding.sale_id !== intent.plan.request.sale_id ||
    binding.plan_hash !== intent.plan.plan_hash
  )
    throw new Error("Состояние не соответствует сохранённому плану.");
  return binding;
}
export async function previewUnitPayment(
  request: UnitPaymentRequest,
  scope: PosStorageScope,
): Promise<UnitPaymentPlan> {
  const validated = unitPaymentRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(
    `${path(request.sale_id)}/preview`,
    validated,
    withAuthSession({ timeout: 8_000 }, true),
  );
  const plan = await parseUnitPaymentPlan(data, request.sale_id, scope);
  assertSession(guard);
  if (canonical(plan.request) !== canonical(validated))
    throw new Error("Сервер вернул другой план.");
  return plan;
}
export async function saveUnitPaymentIntent(
  id: string,
  plan: UnitPaymentPlan,
  scope: PosStorageScope,
): Promise<SavedUnitPaymentIntent> {
  operationId.parse(id);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  await parseUnitPaymentPlan(plan, plan.request.sale_id, scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(
    `${path(plan.request.sale_id)}/intents`,
    { operation_id: id, request: plan.request },
    withAuthSession({ timeout: 8_000 }, true),
  );
  const saved = await parseSavedUnitPaymentIntent(data, plan.request.sale_id, scope, id);
  assertSession(guard);
  if (saved.plan.plan_hash !== plan.plan_hash)
    throw new Error("Сохранён другой план. Проверьте результат операции.");
  return saved;
}
export async function getUnitPaymentIntent(
  saleId: string,
  id: string,
  scope: PosStorageScope,
): Promise<SavedUnitPaymentIntent | null> {
  operationId.parse(id);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  try {
    const { data } = await api.get<unknown>(
      `${path(saleId)}/intents/${id}`,
      withAuthSession({ timeout: 8_000 }, true),
    );
    const saved = await parseSavedUnitPaymentIntent(data, saleId, scope, id);
    assertSession(guard);
    return saved;
  } catch (error) {
    assertSession(guard);
    if (isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}
export async function unitPaymentBindingAction(
  intent: SavedUnitPaymentIntent,
  action: "bind" | "cancel-binding" | "binding",
  scope: PosStorageScope,
): Promise<UnitPaymentBinding | null> {
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  await parseSavedUnitPaymentIntent(
    intent,
    intent.plan.request.sale_id,
    scope,
    intent.operation_id,
  );
  assertSession(guard);
  const url = `${path(intent.plan.request.sale_id)}/intents/${operationId.parse(intent.operation_id)}/${action}`;
  try {
    const { data } =
      action === "binding"
        ? await api.get<unknown>(url, withAuthSession({ timeout: 8_000 }, true))
        : await api.post<unknown>(url, undefined, withAuthSession({ timeout: 8_000 }, true));
    assertSession(guard);
    const binding = parseUnitPaymentBinding(data, intent);
    if (
      (action === "bind" && binding.state !== "bound") ||
      (action === "cancel-binding" && binding.state !== "cancelled")
    )
      throw new Error("Неожиданное состояние подготовки.");
    return binding;
  } catch (error) {
    assertSession(guard);
    if (action === "binding" && isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}
export async function getActiveUnitPayment(
  saleId: string,
  scope: PosStorageScope,
): Promise<{ intent: SavedUnitPaymentIntent | null; binding: UnitPaymentBinding | null }> {
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.get<unknown>(
    `${path(saleId)}/active-binding`,
    withAuthSession({ timeout: 8_000 }, true),
  );
  const active = z
    .object({ sale_id: uuid, intent: z.unknown(), binding: z.unknown() })
    .strict()
    .parse(data);
  if (active.sale_id !== saleId) throw new Error("Состояние относится к другой продаже.");
  if (active.intent === null && active.binding === null) {
    assertSession(guard);
    return { intent: null, binding: null };
  }
  const intent = await parseSavedUnitPaymentIntent(active.intent, saleId, scope);
  const binding = parseUnitPaymentBinding(active.binding, intent);
  assertSession(guard);
  if (binding.state !== "bound") throw new Error("Неожиданное состояние активного плана.");
  return { intent, binding };
}
