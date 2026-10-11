import { z } from "zod";

import { api } from "@/lib/api";

import { capturePosSessionGuard, type PosStorageScope } from "./posStorageScope";

const uuid = z.string().uuid();
const operationId = uuid.regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const cents = (value: string): bigint =>
  /^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/.test(value) ? BigInt(value.replace(".", "")) : -1n;
const positiveMoney = money.refine((value) => cents(value) > 0n);
const quantity = z.number().int().min(1).max(99_999_999_999);
const timestamp = z.string().datetime({ offset: true });
const printable = (max: number) =>
  z
    .string()
    .min(1)
    .max(max)
    .refine(
      (value) =>
        value.trim().length > 0 &&
        Array.from(value).every((character) => {
          const code = character.charCodeAt(0);
          return code >= 32 && (code < 127 || code > 159);
        }),
    );

export const cashConfirmationRequestSchema = z
  .object({
    schema_version: z.literal(2),
    operation_id: operationId,
    sale_id: uuid,
    expected_revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    expected_total_amount: positiveMoney,
    cash_tendered: positiveMoney,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine(
    (value) => cents(value.cash_tendered) >= cents(value.expected_total_amount),
    "Недостаточно наличных для оплаты",
  );
export type CashConfirmationRequest = z.infer<typeof cashConfirmationRequestSchema>;

const packaging = z
  .object({
    version: z.literal(2),
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    units_per_pack: z.number().int().min(2).max(10_000),
    units_per_blister: z.number().int().min(2).max(10_000).nullable(),
    dispense_step: z.number().int().min(1).max(10_000),
  })
  .strict()
  .refine(
    (value) =>
      value.units_per_pack % value.dispense_step === 0 &&
      (value.units_per_blister === null || value.units_per_pack % value.units_per_blister === 0),
  );
const lineShape = {
  inventory_kind: z.literal("physical_unit"),
  id: uuid,
  position: z.number().int().positive(),
  catalog_id: uuid,
  packaging,
  sale_format: z.enum(["pack", "blister", "unit"]),
  quantity,
  base_units: quantity,
  original_retail_unit_price: positiveMoney,
  gross_amount: positiveMoney,
  discount_amount: money,
  net_amount: positiveMoney,
  money_version: z.literal(1),
  currency: z.literal("TJS"),
};
type RetailLine = z.infer<ReturnType<typeof z.object<typeof lineShape>>>;
function lineBalances(line: RetailLine): boolean {
  if (!Number.isSafeInteger(line.quantity) || !Number.isSafeInteger(line.base_units)) return false;
  const size =
    line.sale_format === "pack"
      ? line.packaging.units_per_pack
      : line.sale_format === "blister"
        ? line.packaging.units_per_blister
        : 1;
  return (
    size !== null &&
    Number.isSafeInteger(size) &&
    BigInt(size) * BigInt(line.quantity) === BigInt(line.base_units) &&
    (line.sale_format !== "unit" || line.quantity % line.packaging.dispense_step === 0) &&
    cents(line.original_retail_unit_price) * BigInt(line.quantity) === cents(line.gross_amount) &&
    cents(line.net_amount) + cents(line.discount_amount) === cents(line.gross_amount)
  );
}
const quoteLine = z.object(lineShape).strict().refine(lineBalances);
const resultLine = z
  .object({ ...lineShape, name: printable(500) })
  .strict()
  .refine(lineBalances);
const scopeShape = {
  tenant_id: uuid,
  branch_id: uuid,
  sale_id: uuid,
  register_id: uuid,
  shift_id: uuid,
  cashier_id: uuid,
};
const quoteSchema = z
  .object({
    ...scopeShape,
    schema_version: z.literal(2),
    state: z.literal("draft_quote"),
    lines: z.array(quoteLine).min(1).max(200),
    total_amount: positiveMoney,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine((value) => linesBalance(value.lines, value.total_amount));
function linesBalance(
  lines: { id: string; position: number; net_amount: string }[],
  total: string,
): boolean {
  return (
    new Set(lines.map((line) => line.id)).size === lines.length &&
    lines.every((line, index) => line.position === index + 1) &&
    lines.reduce((sum, line) => sum + cents(line.net_amount), 0n) === cents(total)
  );
}
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
export type SavedPhysicalCashIntent = z.infer<typeof savedSchema>;

const resultSchema = z
  .object({
    ...scopeShape,
    schema_version: z.literal(3),
    document_kind: z.literal("physical_checkout"),
    operation_id: operationId,
    event_id: uuid,
    stock_command_id: uuid,
    created_at: timestamp,
    completed_at: timestamp,
    receipt_number: printable(100),
    receipt_sequence: z.number().int().positive(),
    is_test: z.boolean(),
    lines: z.array(resultLine).min(1).max(200),
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
      linesBalance(value.lines, value.total_amount) &&
      value.payments[0]?.amount === value.total_amount &&
      Date.parse(value.completed_at) >= Date.parse(value.created_at),
  );
export type PhysicalCashCheckoutResult = z.infer<typeof resultSchema>;
const statusShape = { operation_id: operationId, sale_id: uuid };
// Three strict literal-state variants preserve the same contract while reusing
// the union runtime already shipped by the form bundle.
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
export type PhysicalCashOperationStatus = z.infer<typeof statusSchema>;

function assertScope(
  value: z.infer<typeof quoteSchema> | PhysicalCashCheckoutResult,
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
export function parseSavedPhysicalCashIntent(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): SavedPhysicalCashIntent {
  const saved = savedSchema.parse(data);
  const original = cashConfirmationRequestSchema.parse(request);
  if (
    Object.keys(original).some(
      (key) =>
        original[key as keyof CashConfirmationRequest] !==
        saved.confirmation.request[key as keyof CashConfirmationRequest],
    )
  )
    throw new Error("Сохранённая оплата отличается от исходной операции");
  assertScope(saved.confirmation.quote, original, scope);
  return saved;
}
export function parsePhysicalCashCheckoutResult(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): PhysicalCashCheckoutResult {
  const result = resultSchema.parse(data);
  assertScope(result, cashConfirmationRequestSchema.parse(request), scope);
  if (result.operation_id !== request.operation_id)
    throw new Error("Результат относится к другой операции оплаты");
  return result;
}
export function parsePhysicalCashOperationStatus(
  data: unknown,
  request: CashConfirmationRequest,
  scope: PosStorageScope,
): PhysicalCashOperationStatus {
  const status = statusSchema.parse(data);
  if (status.sale_id !== request.sale_id || status.operation_id !== request.operation_id)
    throw new Error("Статус относится к другой операции оплаты");
  if (status.state !== "not_found")
    parseSavedPhysicalCashIntent(status.saved_intent, request, scope);
  if (status.state === "completed") {
    const result = parsePhysicalCashCheckoutResult(status.result, request, scope);
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
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
async function quoteHash(quote: unknown): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalJson(quote)),
  );
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function parsePhysicalCashQuote(
  data: unknown,
  saleId: string,
  branchId: string,
  registerId: string,
  scope: PosStorageScope,
) {
  const parsed = z
    .object({
      quote: quoteSchema,
      revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    })
    .strict()
    .parse(data);
  if (
    parsed.quote.sale_id !== saleId ||
    parsed.quote.branch_id !== branchId ||
    parsed.quote.register_id !== registerId ||
    parsed.quote.tenant_id !== scope.tenantId ||
    parsed.quote.cashier_id !== scope.userId ||
    (await quoteHash(parsed.quote)) !== parsed.revision_hash
  )
    throw new Error("Расчёт не соответствует текущей корзине или кассиру");
  return parsed;
}

async function verifyQuoteHash(intent: SavedPhysicalCashIntent): Promise<void> {
  if (
    (await quoteHash(intent.confirmation.quote)) !==
    intent.confirmation.request.expected_revision_hash
  )
    throw new Error("Сохранённая корзина не соответствует подтверждённой оплате");
}
function assertSession(guard: () => boolean): void {
  if (!guard()) throw new Error("Authentication session changed");
}
// The mixed cash channel shares the exact physical retail and hashing rules.
// Export primitives together to keep the existing physical API unchanged.
export const cashCheckoutContracts = {
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
};
function path(request: CashConfirmationRequest): string {
  return `/sales/${encodeURIComponent(request.sale_id)}/physical-cash`;
}
export async function savePhysicalCashIntent(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<SavedPhysicalCashIntent> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(`${path(validated)}/intent`, validated, {
    signal,
    timeout: 8_000,
  });
  assertSession(guard);
  const saved = parseSavedPhysicalCashIntent(data, validated, scope);
  await verifyQuoteHash(saved);
  assertSession(guard);
  return saved;
}
export async function completePhysicalCash(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<PhysicalCashCheckoutResult> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.post<unknown>(`${path(validated)}/complete`, validated, {
    signal,
    timeout: 8_000,
  });
  assertSession(guard);
  return parsePhysicalCashCheckoutResult(data, validated, scope);
}
export async function getPhysicalCashOperation(
  request: CashConfirmationRequest,
  scope: PosStorageScope,
  signal?: AbortSignal,
): Promise<PhysicalCashOperationStatus> {
  const validated = cashConfirmationRequestSchema.parse(request);
  const guard = capturePosSessionGuard(scope);
  assertSession(guard);
  const { data } = await api.get<unknown>(
    `${path(validated)}/operations/${encodeURIComponent(validated.operation_id)}`,
    { signal, timeout: 8_000 },
  );
  assertSession(guard);
  const status = parsePhysicalCashOperationStatus(data, validated, scope);
  if (status.state !== "not_found") await verifyQuoteHash(status.saved_intent);
  assertSession(guard);
  return status;
}
