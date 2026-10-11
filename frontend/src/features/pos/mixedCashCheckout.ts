import { z } from "zod";

import { api } from "@/lib/api";

import {
  cashCheckoutContracts,
  cashConfirmationRequestSchema,
  type CashConfirmationRequest,
} from "./physicalCashCheckout";
import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";

const {
  uuid,
  operationId,
  money,
  cents,
  positiveMoney,
  timestamp,
  printable,
  quoteLine,
  resultLine,
  scopeShape,
  linesBalance,
  canonicalJson,
  quoteHash,
  assertSession,
} = cashCheckoutContracts;

const legacyQuantityPattern = /^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/;
const legacyThousandths = (value: string): bigint =>
  legacyQuantityPattern.test(value) ? BigInt(value.replace(".", "")) : -1n;
const legacyShape = {
  inventory_kind: z.literal("legacy_pack"),
  id: uuid,
  position: z.number().int().positive(),
  catalog_id: uuid,
  batch_id: uuid,
  quantity: z
    .string()
    .regex(legacyQuantityPattern)
    .refine((value) => legacyThousandths(value) > 0n),
  original_retail_unit_price: positiveMoney,
  gross_amount: money,
  discount_amount: money,
  net_amount: money,
  money_version: z.literal(1),
  currency: z.literal("TJS"),
};
type LegacyLine = z.infer<ReturnType<typeof z.object<typeof legacyShape>>>;

function legacyBalances(line: LegacyLine): boolean {
  // Decimal packs are stored to three places; ties round to an even cent,
  // exactly as the backend Decimal context does, without binary float arithmetic.
  const quantity = legacyThousandths(line.quantity);
  if (quantity <= 0n) return false;
  const product = quantity * cents(line.original_retail_unit_price);
  const whole = product / 1_000n;
  const remainder = product % 1_000n;
  const gross = whole + (remainder > 500n || (remainder === 500n && whole % 2n !== 0n) ? 1n : 0n);
  return (
    gross === cents(line.gross_amount) &&
    cents(line.net_amount) + cents(line.discount_amount) === gross
  );
}
const legacyQuoteLine = z.object(legacyShape).strict().refine(legacyBalances);
const legacyResultLine = z
  .object({ ...legacyShape, name: printable(500) })
  .strict()
  .refine(legacyBalances);
function bothKinds(lines: { inventory_kind: string }[]): boolean {
  // The authoritative quote emits original legacy rows before physical rows.
  const physicalStart = lines.findIndex((line) => line.inventory_kind === "physical_unit");
  return (
    physicalStart > 0 &&
    lines.slice(physicalStart).every((line) => line.inventory_kind === "physical_unit")
  );
}
const quoteSchema = z
  .object({
    ...scopeShape,
    schema_version: z.literal(2),
    state: z.literal("draft_quote"),
    lines: z
      .array(z.union([legacyQuoteLine, quoteLine]))
      .min(2)
      .max(200),
    total_amount: positiveMoney,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine((value) => bothKinds(value.lines) && linesBalance(value.lines, value.total_amount));
const savedSchema = z
  .object({
    schema_version: z.literal(2),
    state: z.literal("saved_draft"),
    accepted_at: timestamp,
    confirmation: z
      .object({
        schema_version: z.literal(2),
        state: z.literal("validated_draft"),
        request: cashConfirmationRequestSchema,
        quote: quoteSchema,
        change_amount: money,
      })
      .strict(),
  })
  .strict()
  .refine(
    ({ confirmation: value }) =>
      value.request.sale_id === value.quote.sale_id &&
      value.request.expected_total_amount === value.quote.total_amount &&
      cents(value.request.cash_tendered) - cents(value.quote.total_amount) ===
        cents(value.change_amount),
  );
export type SavedMixedCashIntent = z.infer<typeof savedSchema>;

const resultSchema = z
  .object({
    ...scopeShape,
    schema_version: z.literal(4),
    document_kind: z.literal("mixed_checkout"),
    operation_id: operationId,
    event_id: uuid,
    stock_command_id: uuid,
    created_at: timestamp,
    completed_at: timestamp,
    receipt_number: printable(100),
    receipt_sequence: z.number().int().positive().safe(),
    is_test: z.literal(false),
    lines: z
      .array(z.union([legacyResultLine, resultLine]))
      .min(2)
      .max(200),
    payments: z
      .array(
        z
          .object({
            id: uuid,
            method: z.literal("cash"),
            amount: positiveMoney,
            currency: z.literal("TJS"),
          })
          .strict(),
      )
      .length(1),
    total_amount: positiveMoney,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine(
    (value) =>
      bothKinds(value.lines) &&
      linesBalance(value.lines, value.total_amount) &&
      value.payments[0]?.amount === value.total_amount &&
      value.payments[0]?.id === value.operation_id &&
      value.event_id === value.operation_id &&
      value.stock_command_id === value.operation_id &&
      Date.parse(value.completed_at) >= Date.parse(value.created_at),
  );
export type MixedCashCheckoutResult = z.infer<typeof resultSchema>;
const statusShape = { operation_id: operationId, sale_id: uuid };
const statusSchema = z.union([
  z.object({ ...statusShape, state: z.literal("not_found") }).strict(),
  z.object({ ...statusShape, state: z.literal("saved_draft"), saved_intent: savedSchema }).strict(),
  z
    .object({
      ...statusShape,
      state: z.literal("completed"),
      saved_intent: savedSchema,
      result: resultSchema,
    })
    .strict(),
]);
export type MixedCashOperationStatus = z.infer<typeof statusSchema>;

function assertScope(
  value: z.infer<typeof quoteSchema> | MixedCashCheckoutResult,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): void {
  if (
    value.tenant_id !== scope.tenantId ||
    value.cashier_id !== scope.userId ||
    value.sale_id !== request.sale_id ||
    value.total_amount !== request.expected_total_amount
  )
    throw new Error("Ответ оплаты относится к другой операции или пользователю");
}
export function parseSavedMixedCashIntent(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): SavedMixedCashIntent {
  const saved = savedSchema.parse(data);
  const original = cashConfirmationRequestSchema.parse(request);
  if (canonicalJson(original) !== canonicalJson(saved.confirmation.request))
    throw new Error("Сохранённая оплата отличается от исходной операции");
  assertScope(saved.confirmation.quote, original, scope);
  return saved;
}
export function parseMixedCashCheckoutResult(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): MixedCashCheckoutResult {
  const result = resultSchema.parse(data);
  const original = cashConfirmationRequestSchema.parse(request);
  assertScope(result, original, scope);
  if (result.operation_id !== original.operation_id)
    throw new Error("Результат относится к другой операции оплаты");
  return result;
}
export function parseMixedCashOperationStatus(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): MixedCashOperationStatus {
  const status = statusSchema.parse(data);
  const original = cashConfirmationRequestSchema.parse(request);
  if (status.sale_id !== original.sale_id || status.operation_id !== original.operation_id)
    throw new Error("Статус относится к другой операции оплаты");
  if (status.state !== "not_found") parseSavedMixedCashIntent(status.saved_intent, original, scope);
  if (status.state === "completed") {
    const result = parseMixedCashCheckoutResult(status.result, original, scope);
    const quote = status.saved_intent.confirmation.quote;
    if (
      ["branch_id", "register_id", "shift_id"].some(
        (key) =>
          result[key as "branch_id" | "register_id" | "shift_id"] !==
          quote[key as "branch_id" | "register_id" | "shift_id"],
      ) ||
      result.lines.length !== quote.lines.length ||
      result.lines.some(
        ({ name: _name, ...line }, index) =>
          canonicalJson(line) !== canonicalJson(quote.lines[index]),
      )
    )
      throw new Error("Результат оплаты отличается от сохранённой корзины");
  }
  return status;
}
export async function parseMixedCashQuote(
  data: unknown,
  saleId: string,
  branchId: string,
  registerId: string,
  scope: PosStorageScope,
) {
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const parsed = z
    .object({ quote: quoteSchema, revision_hash: z.string().regex(/^[0-9a-f]{64}$/) })
    .strict()
    .parse(data);
  if (
    parsed.quote.sale_id !== saleId ||
    parsed.quote.branch_id !== branchId ||
    parsed.quote.register_id !== registerId ||
    parsed.quote.tenant_id !== scope.tenantId ||
    parsed.quote.cashier_id !== scope.userId
  )
    throw new Error("Расчёт не соответствует текущей корзине или кассиру");
  const hash = await quoteHash(parsed.quote);
  assertSession(guard);
  if (hash !== parsed.revision_hash)
    throw new Error("Расчёт не соответствует текущей корзине или кассиру");
  return parsed;
}
async function verifyQuoteHash(intent: SavedMixedCashIntent): Promise<void> {
  if (
    (await quoteHash(intent.confirmation.quote)) !==
    intent.confirmation.request.expected_revision_hash
  )
    throw new Error("Сохранённая корзина не соответствует подтверждённой оплате");
}
function path(request: CashConfirmationRequest): string {
  return `/sales/${encodeURIComponent(request.sale_id)}/mixed-cash`;
}
export async function saveMixedCashIntent(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<SavedMixedCashIntent> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(`${path(validated)}/intent`, validated, {
    signal,
    timeout: 8_000,
  });
  assertSession(guard);
  const saved = parseSavedMixedCashIntent(data, validated, scope);
  await verifyQuoteHash(saved);
  assertSession(guard);
  return saved;
}
export async function completeMixedCash(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<MixedCashCheckoutResult> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(`${path(validated)}/complete`, validated, {
    signal,
    timeout: 8_000,
  });
  assertSession(guard);
  return parseMixedCashCheckoutResult(data, validated, scope);
}
export async function getMixedCashOperation(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<MixedCashOperationStatus> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.get<unknown>(
    `${path(validated)}/operations/${encodeURIComponent(validated.operation_id)}`,
    { signal, timeout: 8_000 },
  );
  assertSession(guard);
  const status = parseMixedCashOperationStatus(data, validated, scope);
  if (status.state !== "not_found") await verifyQuoteHash(status.saved_intent);
  assertSession(guard);
  return status;
}
