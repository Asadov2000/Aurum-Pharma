import { type SaleDetails } from "./types";
import { z } from "zod";
import { hasPermissionForBranch } from "@/features/auth/permissions";
import { api, withAuthSession } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";
import { getCurrentShift } from "./api";
import { generateUuidV4 } from "./operationId";
import { cashCheckoutContracts } from "./physicalCashCheckout";
import { mixedLegacyResultLineSchema } from "./mixedCashCheckout";
import { hasCashRecovery, physicalCashRecoveryKey } from "./physicalCashRecoveryPresence";
import { hasUnitPaymentPreparation } from "./unitPaymentPreparationPresence";
import { capturePosSessionGuard } from "./posStorageScope";
import { getUnitPaymentQuote } from "./unitPaymentPreparation";
import { trainingCheckoutKey } from "./trainingCheckoutPresence";

const {
  uuid,
  operationId,
  money,
  positiveMoney,
  cents,
  printable,
  timestamp,
  quoteLine,
  scopeShape,
  canonicalJson,
  quoteHash,
  linesBalance,
} = cashCheckoutContracts;
const payment = z
  .object({ method: z.enum(["cash", "card", "qr"]), amount: positiveMoney })
  .strict();
export const trainingCheckoutRequestSchema = z
  .object({
    schema_version: z.literal(1),
    operation_id: operationId,
    sale_id: uuid,
    expected_revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    expected_total_amount: positiveMoney,
    simulated_payments: z.array(payment).min(1).max(3),
    cash_tendered: money,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine((r) => {
    const cash = r.simulated_payments.find((p) => p.method === "cash")?.amount ?? "0.00";
    return (
      new Set(r.simulated_payments.map((p) => p.method)).size === r.simulated_payments.length &&
      r.simulated_payments.reduce((s, p) => s + cents(p.amount), 0n) ===
        cents(r.expected_total_amount) &&
      cents(r.cash_tendered) >= cents(cash) &&
      (cents(cash) > 0n || r.cash_tendered === "0.00")
    );
  });
export type TrainingCheckoutRequest = z.infer<typeof trainingCheckoutRequestSchema>;
const scopeSchema = z
  .object({ tenantId: uuid, userId: uuid, branchId: uuid, registerId: uuid, saleId: uuid })
  .strict();
export type TrainingCheckoutScope = z.infer<typeof scopeSchema>;
export interface TrainingContext {
  scope: TrainingCheckoutScope;
  active: () => boolean;
  isTest: () => boolean;
  writable: () => boolean;
}
const legacyShape = mixedLegacyResultLineSchema.shape;
const legacyQuote = z
  .object({
    inventory_kind: legacyShape.inventory_kind,
    id: legacyShape.id,
    position: legacyShape.position,
    catalog_id: legacyShape.catalog_id,
    batch_id: legacyShape.batch_id,
    quantity: legacyShape.quantity,
    original_retail_unit_price: legacyShape.original_retail_unit_price,
    gross_amount: money,
    discount_amount: money,
    net_amount: money,
    money_version: z.literal(1),
    currency: z.literal("TJS"),
  })
  .strict()
  .refine((l) => mixedLegacyResultLineSchema.safeParse({ ...l, name: "training" }).success);
const quoteSchema = z
  .object({
    ...scopeShape,
    schema_version: z.literal(2),
    state: z.literal("draft_quote"),
    lines: z
      .array(z.union([legacyQuote, quoteLine]))
      .min(1)
      .max(200),
    total_amount: positiveMoney,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine(
    (q) =>
      q.lines.some((l) => l.inventory_kind === "physical_unit") &&
      linesBalance(q.lines, q.total_amount),
  );
export type TrainingQuote = z.infer<typeof quoteSchema>;
const headers = {
  ...scopeShape,
  schema_version: z.literal(1),
  is_test: z.literal(true),
  operation_id: operationId,
  receipt_number: printable(100).refine((s) => s.startsWith("TEST-")),
  receipt_sequence: z.number().int().positive().safe(),
  completed_at: timestamp,
  total_amount: positiveMoney,
  currency: z.literal("TJS"),
};
const physicalReceipt = z
  .object({
    inventory_kind: z.literal("physical_unit"),
    position: quoteLine.shape.position,
    name: printable(500),
    packaging: quoteLine.shape.packaging,
    sale_format: quoteLine.shape.sale_format,
    quantity: quoteLine.shape.quantity,
    base_units: quoteLine.shape.base_units,
    original_retail_unit_price: positiveMoney,
    gross_amount: positiveMoney,
    discount_amount: money,
    net_amount: positiveMoney,
    money_version: z.literal(1),
    currency: z.literal("TJS"),
    unit_label: printable(30),
  })
  .strict();
const legacyReceipt = z
  .object({
    inventory_kind: z.literal("legacy_pack"),
    position: legacyShape.position,
    name: printable(500),
    quantity: legacyShape.quantity,
    original_retail_unit_price: positiveMoney,
    gross_amount: money,
    discount_amount: money,
    net_amount: money,
    money_version: z.literal(1),
    currency: z.literal("TJS"),
  })
  .strict();
const resultSchema = z
  .object({
    ...headers,
    document_kind: z.literal("training_checkout"),
    request: trainingCheckoutRequestSchema,
    receipt: z
      .object({
        ...headers,
        document_kind: z.literal("training_sale_receipt"),
        lines: z
          .array(z.union([legacyReceipt, physicalReceipt]))
          .min(1)
          .max(200),
        simulated_payments: z.array(payment).min(1).max(3),
        cash_tendered: money,
        change_amount: money,
      })
      .strict(),
    // Stock simulation is server-owned, RAM-only and never passed to stock/payment APIs.
    simulation: z
      .object({
        schema_version: z.literal(1),
        state: z.literal("simulated_training_sale"),
        is_test: z.literal(true),
        stock_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
        quote: quoteSchema,
        total_amount: positiveMoney,
        physical_before: z.array(z.unknown()).max(10000),
        physical_after: z.array(z.unknown()).max(10000),
        legacy_before: z.array(z.unknown()).max(10000),
        legacy_after: z.array(z.unknown()).max(10000),
      })
      .strict(),
  })
  .strict();
export type TrainingCheckoutResult = z.infer<typeof resultSchema>;
export type TrainingRecovery =
  | { state: "not_found" }
  | { state: "completed"; result: TrainingCheckoutResult };
const pendingSchema = z
  .object({ version: z.literal(1), scope: scopeSchema, request: trainingCheckoutRequestSchema })
  .strict();
export type PendingTrainingCheckout = z.infer<typeof pendingSchema>;
function fail(): never {
  throw new Error("Учебная операция не подтверждена. Сохранённый запрос оставлен для проверки.");
}
function checkFor(c: TrainingContext, write = false): () => void {
  const s = scopeSchema.parse(c.scope),
    active = capturePosSessionGuard(s);
  return () => {
    const { user, accessToken } = useAuthStore.getState();
    if (hasCashRecovery(s.saleId, s) || hasUnitPaymentPreparation(s.saleId, s)) fail();
    if (
      !active() ||
      !c.active() ||
      !c.isTest() ||
      !user ||
      !accessToken ||
      !hasPermissionForBranch(user, "pos.sell", s.branchId) ||
      (write && (!c.writable() || user.support_access?.is_read_only))
    )
      fail();
  };
}
function assertScope(
  q: z.infer<ReturnType<typeof z.object<typeof scopeShape>>>,
  s: TrainingCheckoutScope,
): void {
  if (
    q.tenant_id !== s.tenantId ||
    q.cashier_id !== s.userId ||
    q.branch_id !== s.branchId ||
    q.register_id !== s.registerId ||
    q.sale_id !== s.saleId
  )
    fail();
}
export function loadTrainingCheckout(s: TrainingCheckoutScope): PendingTrainingCheckout | null {
  s = scopeSchema.parse(s);
  try {
    const raw = window.localStorage.getItem(trainingCheckoutKey(s.saleId, s));
    if (raw === null) return null;
    const p = pendingSchema.parse(JSON.parse(raw) as unknown);
    if (canonicalJson(p.scope) !== canonicalJson(s) || p.request.sale_id !== s.saleId) fail();
    return p;
  } catch {
    return fail();
  }
}
async function exclusive<T>(
  c: TrainingContext,
  write: boolean,
  run: (check: () => void) => Promise<T>,
): Promise<T> {
  const check = checkFor(c, write);
  check();
  if (!navigator.locks?.request) fail();
  return navigator.locks.request(physicalCashRecoveryKey(c.scope.saleId, c.scope), async () => {
    check();
    if (
      hasCashRecovery(c.scope.saleId, c.scope) ||
      hasUnitPaymentPreparation(c.scope.saleId, c.scope)
    )
      fail();
    const p = loadTrainingCheckout(c.scope),
      before = canonicalJson(p);
    const unchanged = () => {
      check();
      if (canonicalJson(loadTrainingCheckout(c.scope)) !== before) fail();
    };
    return run(unchanged);
  });
}
export async function getTrainingQuote(
  c: TrainingContext,
): Promise<{ quote: TrainingQuote; revision_hash: string }> {
  c = { ...c, scope: scopeSchema.parse(c.scope) };
  return exclusive(c, true, async (check) => {
    if (loadTrainingCheckout(c.scope)) fail();
    const raw = await getUnitPaymentQuote(c.scope.saleId, c.scope);
    check();
    const quote = quoteSchema.parse(raw.quote);
    assertScope(quote, c.scope);
    return { quote, revision_hash: raw.revision_hash };
  });
}
export async function parseTrainingResult(
  data: unknown,
  p: PendingTrainingCheckout,
  check: () => void,
): Promise<TrainingCheckoutResult> {
  const r = resultSchema.parse(data),
    q = r.simulation.quote;
  assertScope(r, p.scope);
  assertScope(q, p.scope);
  if (
    canonicalJson(r.request) !== canonicalJson(p.request) ||
    r.operation_id !== p.request.operation_id ||
    r.shift_id !== q.shift_id ||
    r.total_amount !== p.request.expected_total_amount ||
    r.simulation.total_amount !== r.total_amount ||
    q.total_amount !== r.total_amount
  )
    fail();
  for (const key of Object.keys(headers) as (keyof typeof headers)[])
    if (r[key] !== r.receipt[key]) fail();
  if ((await quoteHash(q)) !== p.request.expected_revision_hash) fail();
  check();
  const cash = p.request.simulated_payments.find((x) => x.method === "cash")?.amount ?? "0.00";
  if (
    canonicalJson(r.receipt.simulated_payments) !== canonicalJson(p.request.simulated_payments) ||
    r.receipt.cash_tendered !== p.request.cash_tendered ||
    cents(r.receipt.change_amount) !== cents(p.request.cash_tendered) - cents(cash) ||
    r.receipt.lines.length !== q.lines.length
  )
    fail();
  q.lines.forEach((line, i) => {
    const printed = r.receipt.lines[i];
    if (!printed || printed.inventory_kind !== line.inventory_kind) fail();
    for (const [key, value] of Object.entries(line))
      if (
        !["id", "catalog_id", "batch_id"].includes(key) &&
        canonicalJson(value) !== canonicalJson(Reflect.get(printed, key))
      )
        fail();
    if (line.inventory_kind === "physical_unit" && printed.inventory_kind === "physical_unit") {
      const label =
        line.sale_format === "pack"
          ? "уп."
          : line.sale_format === "blister"
            ? "блист."
            : {
                tablet: "табл.",
                capsule: "капс.",
                ampoule: "амп.",
                sachet: "саше",
                suppository: "супп.",
              }[line.packaging.unit];
      if (printed.unit_label !== label) fail();
    }
  });
  return r;
}
async function read(
  c: TrainingContext,
  p: PendingTrainingCheckout,
  check: () => void,
): Promise<TrainingRecovery> {
  check();
  const { data } = await api.get<unknown>(
    `/sales/${c.scope.saleId}/training/operations/${p.request.operation_id}`,
    withAuthSession({ timeout: 8000 }, true),
  );
  check();
  const envelope = z
    .union([
      z
        .object({ state: z.literal("not_found"), operation_id: operationId, sale_id: uuid })
        .strict(),
      z
        .object({
          state: z.literal("completed"),
          operation_id: operationId,
          sale_id: uuid,
          result: z.unknown(),
        })
        .strict(),
    ])
    .parse(data);
  if (envelope.operation_id !== p.request.operation_id || envelope.sale_id !== p.scope.saleId)
    fail();
  return envelope.state === "not_found"
    ? { state: "not_found" }
    : { state: "completed", result: await parseTrainingResult(envelope.result, p, check) };
}
/** Reopen/reload performs only GET and never removes the durable command. */
export async function inspectTrainingCheckout(
  c: TrainingContext,
): Promise<TrainingRecovery | null> {
  c = { ...c, scope: scopeSchema.parse(c.scope) };
  return exclusive(c, false, async (check) => {
    const p = loadTrainingCheckout(c.scope);
    return p ? read(c, p, check) : null;
  });
}
async function authority(c: TrainingContext, check: () => void): Promise<void> {
  check();
  const shift = await getCurrentShift(c.scope.registerId);
  check();
  if (
    !shift ||
    shift.status !== "open" ||
    shift.opened_by_user_id !== c.scope.userId ||
    shift.branch_id !== c.scope.branchId ||
    shift.register_id !== c.scope.registerId ||
    shift.currency !== "TJS"
  )
    fail();
}
async function send(
  c: TrainingContext,
  p: PendingTrainingCheckout,
  check: () => void,
): Promise<TrainingCheckoutResult> {
  check();
  const { data } = await api.post<unknown>(
    `/sales/${c.scope.saleId}/training/complete`,
    p.request,
    withAuthSession({ timeout: 8000 }),
  );
  check();
  return parseTrainingResult(data, p, check);
}
export async function startTrainingCheckout(
  c: TrainingContext,
  request: Omit<TrainingCheckoutRequest, "operation_id">,
): Promise<TrainingCheckoutResult> {
  c = { ...c, scope: scopeSchema.parse(c.scope) };
  const p = pendingSchema.parse({
    version: 1,
    scope: c.scope,
    request: { ...structuredClone(request), operation_id: generateUuidV4() },
  });
  if (p.request.sale_id !== c.scope.saleId) fail();
  return exclusive(c, true, async (check) => {
    if (loadTrainingCheckout(c.scope)) fail();
    await authority(c, check);

    if (p.request.sale_id !== c.scope.saleId) fail();
    check();
    const raw = JSON.stringify(p),
      key = trainingCheckoutKey(c.scope.saleId, c.scope);
    try {
      window.localStorage.setItem(key, raw);
      if (window.localStorage.getItem(key) !== raw) fail();
    } catch {
      return fail();
    }
    const active = checkFor(c, true),
      same = () => {
        active();
        if (canonicalJson(loadTrainingCheckout(c.scope)) !== canonicalJson(p)) fail();
      };
    return send(c, p, same);
  });
}
/** Only an explicit retry after an authoritative not_found GET reuses the exact request. */
export async function retryTrainingCheckout(c: TrainingContext): Promise<TrainingCheckoutResult> {
  c = { ...c, scope: scopeSchema.parse(c.scope) };
  return exclusive(c, true, async (check) => {
    const p = loadTrainingCheckout(c.scope);
    if (!p) fail();
    const recovered = await read(c, p, check);
    if (recovered.state === "completed") return recovered.result;
    await authority(c, check);
    return send(c, p, check);
  });
}
export async function acknowledgeTrainingCheckout(c: TrainingContext): Promise<void> {
  c = { ...c, scope: scopeSchema.parse(c.scope) };
  return exclusive(c, false, async (check) => {
    const p = loadTrainingCheckout(c.scope);
    if (!p) fail();
    const recovered = await read(c, p, check);
    if (recovered.state !== "completed") fail();
    check();
    try {
      const key = trainingCheckoutKey(c.scope.saleId, c.scope);
      window.localStorage.removeItem(key);
      if (window.localStorage.getItem(key) !== null) fail();
    } catch {
      fail();
    }
  });
}

/** Match the saved test sale without treating simulated allocations as real payments. */
export function matchesTrainingCompletedSale(
  sale: SaleDetails,
  result: TrainingCheckoutResult,
): boolean {
  return (
    sale.is_test === true &&
    sale.status === "completed" &&
    sale.id === result.sale_id &&
    sale.tenant_id === result.tenant_id &&
    sale.cashier_user_id === result.cashier_id &&
    sale.branch_id === result.branch_id &&
    sale.register_id === result.register_id &&
    sale.shift_id === result.shift_id &&
    sale.total_amount === result.total_amount &&
    sale.receipt_number === result.receipt_number &&
    sale.payments.length === 0
  );
}
