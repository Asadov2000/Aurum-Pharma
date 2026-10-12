import { z } from "zod";

import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { api } from "@/lib/api";

const uuid = z.string().uuid();
const operationId = uuid.regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
export const refundCents = (value: string): bigint => BigInt(value.replace(".", ""));
/** Canonical server quantities are whole thousandths; no floating point conversion. */
export const mixedQuantity = (value: string): bigint => {
  if (!/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/.test(value)) throw new Error("Некорректное количество.");
  return BigInt(value.replace(".", ""));
};
const legacyOffset = z.string().regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/);
const legacyQuantity = legacyOffset.refine((value) => mixedQuantity(value) > 0n);
const whole = z.number().int().positive().safe().max(99_999_999_999);
const offset = z.number().int().nonnegative().safe();
const format = z.enum(["unit", "blister", "pack"]);
const packaging = z
  .object({
    version: z.literal(2),
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    units_per_pack: z.number().int().min(2).max(10_000),
    units_per_blister: z.number().int().min(2).max(10_000).nullable(),
    dispense_step: z.number().int().positive().max(10_000),
  })
  .strict()
  .refine(
    (value) =>
      value.units_per_pack % value.dispense_step === 0 &&
      (value.units_per_blister === null || value.units_per_pack % value.units_per_blister === 0),
  );
const legacyItem = z
  .object({ inventory_kind: z.literal("legacy_pack"), line_id: uuid, quantity: legacyQuantity })
  .strict();
const physicalItem = z
  .object({
    inventory_kind: z.literal("physical_unit"),
    line_id: uuid,
    sale_format: format,
    quantity: whole,
  })
  .strict();
// Strict literal tags keep the contract discriminated without adding another
// Zod parser implementation to the shared forms chunk loaded during login.
const itemSchema = z.union([legacyItem, physicalItem]);
const itemsSchema = z
  .array(itemSchema)
  .min(1)
  .max(200)
  .refine((items) => new Set(items.map((item) => item.line_id)).size === items.length);
export type MixedRefundItem = z.infer<typeof itemSchema>;
export const refundReasonSchema = z.enum([
  "dispensing_error",
  "duplicate_sale",
  "pricing_error",
  "quality_issue",
  "damaged_package",
  "customer_cancelled",
  "other",
]);
export const mixedRefundRequestSchema = z
  .object({
    operation_id: operationId,
    shift_id: uuid,
    items: itemsSchema,
    expected_total_amount: money.refine((value) => refundCents(value) > 0n),
    expected_revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    reason_code: refundReasonSchema.nullable(),
    comment: z.string().max(500).nullable(),
  })
  .strict();
export type MixedRefundRequest = z.infer<typeof mixedRefundRequestSchema>;
export type MixedRefundInput = Omit<MixedRefundRequest, "operation_id">;
export const mixedZeroReturnRequestSchema = mixedRefundRequestSchema
  .omit({ shift_id: true, expected_total_amount: true })
  .strict();
export type MixedZeroReturnRequest = z.infer<typeof mixedZeroReturnRequestSchema>;
export type MixedZeroReturnInput = Omit<MixedZeroReturnRequest, "operation_id">;

const optionBase = {
  line_id: uuid,
  name: z.string().min(1).max(500),
  original_retail_unit_price: money,
};
const legacyOption = z
  .object({
    ...optionBase,
    inventory_kind: z.literal("legacy_pack"),
    unit_label: z.literal("уп."),
    sold_quantity: legacyQuantity,
    returned_quantity: legacyOffset,
    remaining_quantity: legacyOffset,
  })
  .strict();
const physicalOption = z
  .object({
    ...optionBase,
    inventory_kind: z.literal("physical_unit"),
    unit_label: z.string().min(1).max(30),
    packaging,
    sold_base_units: whole,
    returned_base_units: offset,
    remaining_base_units: offset,
    original_sale_format: format,
  })
  .strict();
const optionsSchema = z
  .object({
    parent_sale_id: uuid,
    branch_id: uuid,
    register_id: uuid,
    original_receipt_number: z.string().min(1).max(100),
    reason_mode: z.enum(["off", "optional", "required", "required_with_text"]),
    lines: z
      .array(z.union([legacyOption, physicalOption]))
      .min(1)
      .max(200),
  })
  .strict()
  .refine(
    (options) =>
      new Set(options.lines.map((line) => line.line_id)).size === options.lines.length &&
      options.lines.every((line) =>
        line.inventory_kind === "legacy_pack"
          ? mixedQuantity(line.returned_quantity) + mixedQuantity(line.remaining_quantity) ===
            mixedQuantity(line.sold_quantity)
          : BigInt(line.returned_base_units) + BigInt(line.remaining_base_units) ===
            BigInt(line.sold_base_units),
      ),
  );
export type MixedRefundOptions = z.infer<typeof optionsSchema>;
const amounts = { gross_amount: money, discount_amount: money, net_amount: money };
const legacyQuoteLine = legacyItem.extend({ offset: legacyOffset, ...amounts }).strict();
const physicalQuoteLine = physicalItem.extend({ base_units: whole, offset, ...amounts }).strict();
const balanced = (line: {
  gross_amount: string;
  discount_amount: string;
  net_amount: string;
}): boolean =>
  refundCents(line.net_amount) + refundCents(line.discount_amount) ===
  refundCents(line.gross_amount);
const quoteSchema = z
  .object({
    parent_sale_id: uuid,
    revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    total_amount: money,
    currency: z.literal("TJS"),
    lines: z
      .array(z.union([legacyQuoteLine, physicalQuoteLine]))
      .min(1)
      .max(200),
  })
  .strict()
  .refine(
    (quote) =>
      new Set(quote.lines.map((line) => line.line_id)).size === quote.lines.length &&
      quote.lines.every(balanced) &&
      quote.lines.reduce((sum, line) => sum + refundCents(line.net_amount), 0n) ===
        refundCents(quote.total_amount),
  );
export type MixedRefundQuote = z.infer<typeof quoteSchema>;
const receiptBase = {
  ...amounts,
  original_line_id: uuid,
  position: z.number().int().positive().safe(),
  original_position: z.number().int().positive().safe(),
  name: z.string().min(1).max(500),
  original_retail_unit_price: money,
};
const legacyReceiptLine = z
  .object({
    ...receiptBase,
    inventory_kind: z.literal("legacy_pack"),
    quantity: legacyQuantity,
    offset: legacyOffset,
  })
  .strict();
const physicalReceiptLine = z
  .object({
    ...receiptBase,
    inventory_kind: z.literal("physical_unit"),
    packaging,
    original_sale_format: format,
    sale_format: format,
    quantity: whole,
    base_units: whole,
    offset,
  })
  .strict();
const receiptSchema = z
  .object({
    schema_version: z.literal(4),
    document_kind: z.literal("mixed_cash_refund_receipt"),
    parent_sale_id: uuid,
    parent_receipt_number: z.string().min(1).max(100),
    receipt_number: z.string().min(1).max(100),
    receipt_sequence: z.number().int().positive().safe(),
    completed_at: z.string().datetime({ offset: true }),
    is_test: z.literal(false),
    lines: z
      .array(z.union([legacyReceiptLine, physicalReceiptLine]))
      .min(1)
      .max(200),
    payment_method: z.literal("cash"),
    total_amount: money.refine((value) => refundCents(value) > 0n),
    currency: z.literal("TJS"),
  })
  .strict()
  .refine(
    (receipt) =>
      new Set(receipt.lines.map((line) => line.original_line_id)).size === receipt.lines.length &&
      receipt.lines.every(
        (line, index) =>
          line.position === index + 1 &&
          (index === 0 || line.original_position > receipt.lines[index - 1]!.original_position) &&
          balanced(line) &&
          (line.inventory_kind === "legacy_pack" ||
            physicalUnits(line) === BigInt(line.base_units)),
      ) &&
      receipt.lines.reduce((sum, line) => sum + refundCents(line.net_amount), 0n) ===
        refundCents(receipt.total_amount),
  );
const completedSchema = z
  .object({
    state: z.literal("completed"),
    operation_id: operationId,
    parent_sale_id: uuid,
    sale_id: uuid,
    total_amount: money,
    receipt: receiptSchema,
  })
  .strict();
const statusSchema = z.union([
  z
    .object({ state: z.literal("not_found"), operation_id: operationId, parent_sale_id: uuid })
    .strict(),
  completedSchema,
]);
export type MixedRefundCompleted = z.infer<typeof completedSchema>;
export type MixedRefundStatus = z.infer<typeof statusSchema>;
const zeroLegacyLine = legacyItem.extend({ offset: legacyOffset }).strict();
const zeroPhysicalLine = physicalItem.extend({ base_units: whole, offset }).strict();
const zeroPreparedSchema = z
  .object({
    version: z.literal(1),
    kind: z.literal("mixed_zero_return"),
    operation_id: operationId,
    parent_sale_id: uuid,
    tenant_id: uuid,
    branch_id: uuid,
    actor_id: uuid,
    state: z.literal("prepared_quarantine"),
    currency: z.literal("TJS"),
    total_amount: z.literal("0.00"),
    lines: z
      .array(z.union([zeroLegacyLine, zeroPhysicalLine]))
      .min(1)
      .max(200),
  })
  .strict()
  .refine((value) => new Set(value.lines.map((line) => line.line_id)).size === value.lines.length);
const zeroStatusSchema = z.union([statusSchema.options[0], zeroPreparedSchema]);
export type MixedZeroReturnPrepared = z.infer<typeof zeroPreparedSchema>;
export type MixedZeroReturnStatus = z.infer<typeof zeroStatusSchema>;
export type MixedReturnResult = MixedRefundCompleted | MixedZeroReturnPrepared;
export type MixedReturnStatus = MixedRefundStatus | MixedZeroReturnStatus;

export interface MixedRefundApiScope extends PosStorageScope {
  branchId: string;
  registerId: string;
}
function physicalUnits(line: {
  sale_format: "unit" | "blister" | "pack";
  quantity: number;
  packaging: z.infer<typeof packaging>;
}): bigint | null {
  const size =
    line.sale_format === "pack"
      ? line.packaging.units_per_pack
      : line.sale_format === "blister"
        ? line.packaging.units_per_blister
        : 1;
  if (
    size === null ||
    (line.sale_format === "unit" && line.quantity % line.packaging.dispense_step !== 0)
  )
    return null;
  return BigInt(line.quantity) * BigInt(size);
}
function matches(
  item: MixedRefundItem,
  line: MixedRefundQuote["lines"][number] | MixedRefundCompleted["receipt"]["lines"][number],
): boolean {
  const lineId = "line_id" in line ? line.line_id : line.original_line_id;
  return (
    lineId === item.line_id &&
    line.inventory_kind === item.inventory_kind &&
    line.quantity === item.quantity &&
    (item.inventory_kind === "legacy_pack" ||
      (line.inventory_kind === "physical_unit" && line.sale_format === item.sale_format))
  );
}
export function validateMixedRefundQuote(
  quote: MixedRefundQuote,
  items: MixedRefundItem[],
  options: MixedRefundOptions,
): void {
  const parsed = quoteSchema.parse(quote);
  const selected = itemsSchema.parse(items);
  const source = optionsSchema.parse(options);
  let previous = -1;
  if (
    parsed.parent_sale_id !== source.parent_sale_id ||
    parsed.lines.length !== selected.length ||
    !selected.every((item) => parsed.lines.some((line) => matches(item, line)))
  )
    throw new Error("Расчёт относится к другому набору товаров.");
  for (const line of parsed.lines) {
    const index = source.lines.findIndex((original) => original.line_id === line.line_id);
    const original = source.lines[index];
    if (!original || index <= previous || original.inventory_kind !== line.inventory_kind)
      throw new Error("Расчёт не соответствует исходному чеку.");
    previous = index;
    if (line.inventory_kind === "legacy_pack" && original.inventory_kind === "legacy_pack") {
      if (
        line.offset !== original.returned_quantity ||
        mixedQuantity(line.quantity) > mixedQuantity(original.remaining_quantity)
      )
        throw new Error("Количество возврата изменилось.");
    } else if (
      line.inventory_kind === "physical_unit" &&
      original.inventory_kind === "physical_unit"
    ) {
      if (
        physicalUnits({ ...line, packaging: original.packaging }) !== BigInt(line.base_units) ||
        line.offset !== original.returned_base_units ||
        BigInt(line.base_units) > BigInt(original.remaining_base_units)
      )
        throw new Error("Количество возврата изменилось.");
    }
  }
}
export function parseMixedRefundStatus(
  data: unknown,
  parentId: string,
  request: MixedRefundRequest,
  options?: MixedRefundOptions,
): MixedRefundStatus {
  const status = statusSchema.parse(data);
  if (status.operation_id !== request.operation_id || status.parent_sale_id !== parentId)
    throw new Error("Ответ относится к другой операции возврата.");
  if (status.state === "not_found") return status;
  if (
    status.sale_id === parentId ||
    status.receipt.parent_sale_id !== parentId ||
    status.total_amount !== request.expected_total_amount ||
    status.receipt.total_amount !== status.total_amount ||
    status.receipt.lines.length !== request.items.length ||
    !request.items.every((item) => status.receipt.lines.some((line) => matches(item, line)))
  )
    throw new Error("Сохранённый чек не соответствует подтверждённому возврату.");
  if (options) {
    const original = optionsSchema.parse(options);
    if (
      original.parent_sale_id !== parentId ||
      original.original_receipt_number !== status.receipt.parent_receipt_number
    )
      throw new Error("Сохранённый чек не соответствует исходной продаже.");
    for (const line of status.receipt.lines) {
      const source = original.lines[line.original_position - 1];
      if (
        !source ||
        source.line_id !== line.original_line_id ||
        source.inventory_kind !== line.inventory_kind ||
        source.name !== line.name ||
        source.original_retail_unit_price !== line.original_retail_unit_price
      )
        throw new Error("Сохранённый чек не соответствует исходной продаже.");
      if (line.inventory_kind === "legacy_pack" && source.inventory_kind === "legacy_pack") {
        if (
          mixedQuantity(line.offset) + mixedQuantity(line.quantity) >
          mixedQuantity(source.returned_quantity)
        )
          throw new Error("Возврат не подтверждён исходной продажей.");
      } else if (
        line.inventory_kind === "physical_unit" &&
        source.inventory_kind === "physical_unit"
      ) {
        if (
          JSON.stringify(line.packaging) !== JSON.stringify(source.packaging) ||
          line.original_sale_format !== source.original_sale_format ||
          BigInt(line.offset) + BigInt(line.base_units) > BigInt(source.returned_base_units)
        )
          throw new Error("Возврат не подтверждён исходной продажей.");
      }
    }
  }
  return status;
}
const path = (parentId: string): string =>
  `/sales/${encodeURIComponent(parentId)}/mixed-cash-refunds`;
function requireActive(active: () => boolean): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
}
export async function getMixedRefundOptions(
  parentId: string,
  branchId: string,
  registerId: string,
  scope: PosStorageScope,
): Promise<MixedRefundOptions> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.get<unknown>(`${path(parentId)}/options`, { timeout: 8_000 });
  requireActive(active);
  const options = optionsSchema.parse(data);
  if (
    options.parent_sale_id !== parentId ||
    options.branch_id !== branchId ||
    options.register_id !== registerId
  )
    throw new Error("Возврат относится к другой аптеке или кассе.");
  return options;
}
export async function quoteMixedRefund(
  parentId: string,
  items: MixedRefundItem[],
  scope: PosStorageScope,
): Promise<MixedRefundQuote> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const selected = itemsSchema.parse(items);
  const { data } = await api.post<unknown>(
    `${path(parentId)}/quote`,
    { items: selected },
    { timeout: 8_000 },
  );
  requireActive(active);
  const quote = quoteSchema.parse(data);
  if (
    quote.parent_sale_id !== parentId ||
    quote.lines.length !== selected.length ||
    !selected.every((item) => quote.lines.some((line) => matches(item, line)))
  )
    throw new Error("Расчёт относится к другому набору товаров.");
  return quote;
}
async function validateCompleted(
  data: unknown,
  parentId: string,
  request: MixedRefundRequest,
  scope: MixedRefundApiScope,
): Promise<MixedRefundStatus> {
  const status = parseMixedRefundStatus(data, parentId, request);
  if (status.state === "not_found") return status;
  const options = await getMixedRefundOptions(parentId, scope.branchId, scope.registerId, scope);
  return parseMixedRefundStatus(status, parentId, request, options);
}
export async function getMixedRefundStatus(
  parentId: string,
  request: MixedRefundRequest,
  scope: MixedRefundApiScope,
): Promise<MixedRefundStatus> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = mixedRefundRequestSchema.parse(request);
  const { data } = await api.get<unknown>(
    `${path(parentId)}/operations/${validated.operation_id}`,
    { timeout: 8_000 },
  );
  requireActive(active);
  const result = await validateCompleted(data, parentId, validated, scope);
  requireActive(active);
  return result;
}
export async function completeMixedRefund(
  parentId: string,
  request: MixedRefundRequest,
  scope: MixedRefundApiScope,
): Promise<MixedRefundCompleted> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = mixedRefundRequestSchema.parse(request);
  const { data } = await api.post<unknown>(`${path(parentId)}/complete`, validated, {
    timeout: 15_000,
  });
  requireActive(active);
  const result = await validateCompleted(data, parentId, validated, scope);
  requireActive(active);
  if (result.state !== "completed") throw new Error("Возврат ещё не подтверждён.");
  return result;
}

/** A saved quarantine result is verified against current server history, never local success data. */
export function parseMixedZeroReturnStatus(
  data: unknown,
  parentId: string,
  request: MixedZeroReturnRequest,
  scope: MixedRefundApiScope,
  options?: MixedRefundOptions,
  reviewedQuote?: MixedRefundQuote,
): MixedZeroReturnStatus {
  const status = zeroStatusSchema.parse(data);
  if (status.operation_id !== request.operation_id || status.parent_sale_id !== parentId)
    throw new Error("Ответ относится к другой операции возврата.");
  if (status.state === "not_found") return status;
  if (
    status.tenant_id !== scope.tenantId ||
    status.actor_id !== scope.userId ||
    status.branch_id !== scope.branchId ||
    !options ||
    options.parent_sale_id !== parentId ||
    options.branch_id !== scope.branchId ||
    options.register_id !== scope.registerId ||
    status.lines.length !== request.items.length
  )
    throw new Error("Подтверждение карантина относится к другому аккаунту или набору товаров.");
  const source = optionsSchema.parse(options);
  let previous = -1;
  for (const line of status.lines) {
    const item = request.items.find((candidate) => candidate.line_id === line.line_id);
    const index = source.lines.findIndex((candidate) => candidate.line_id === line.line_id);
    const original = source.lines[index];
    if (
      !item ||
      !original ||
      index <= previous ||
      item.inventory_kind !== line.inventory_kind ||
      original.inventory_kind !== line.inventory_kind ||
      item.quantity !== line.quantity
    )
      throw new Error("Подтверждение карантина не соответствует выбранным товарам.");
    previous = index;
    if (line.inventory_kind === "legacy_pack" && original.inventory_kind === "legacy_pack") {
      if (
        mixedQuantity(line.offset) + mixedQuantity(line.quantity) >
        mixedQuantity(original.returned_quantity)
      )
        throw new Error("Подтверждение карантина не соответствует исходной продаже.");
    } else if (
      line.inventory_kind === "physical_unit" &&
      item.inventory_kind === "physical_unit" &&
      original.inventory_kind === "physical_unit"
    ) {
      if (
        line.sale_format !== item.sale_format ||
        physicalUnits({ ...line, packaging: original.packaging }) !== BigInt(line.base_units) ||
        BigInt(line.offset) + BigInt(line.base_units) > BigInt(original.returned_base_units)
      )
        throw new Error("Подтверждение карантина не соответствует выбранным товарам.");
    }
  }
  if (reviewedQuote) {
    const quote = quoteSchema.parse(reviewedQuote);
    if (
      quote.parent_sale_id !== parentId ||
      quote.revision_hash !== request.expected_revision_hash ||
      quote.total_amount !== "0.00" ||
      quote.lines.length !== status.lines.length ||
      !quote.lines.every((line, index) => {
        const confirmed = status.lines[index];
        return (
          confirmed &&
          line.line_id === confirmed.line_id &&
          line.inventory_kind === confirmed.inventory_kind &&
          line.quantity === confirmed.quantity &&
          line.offset === confirmed.offset &&
          (line.inventory_kind === "legacy_pack" ||
            (confirmed.inventory_kind === "physical_unit" &&
              line.sale_format === confirmed.sale_format &&
              line.base_units === confirmed.base_units))
        );
      })
    )
      throw new Error("Подтверждение карантина не соответствует проверенному расчёту.");
  }
  return status;
}
const zeroPath = (parentId: string): string =>
  `/sales/${encodeURIComponent(parentId)}/mixed-zero-returns`;
async function validateZeroCompleted(
  data: unknown,
  parentId: string,
  request: MixedZeroReturnRequest,
  scope: MixedRefundApiScope,
  reviewedQuote?: MixedRefundQuote,
): Promise<MixedZeroReturnStatus> {
  const status = zeroStatusSchema.parse(data);
  const options =
    status.state === "prepared_quarantine"
      ? await getMixedRefundOptions(parentId, scope.branchId, scope.registerId, scope)
      : undefined;
  return parseMixedZeroReturnStatus(status, parentId, request, scope, options, reviewedQuote);
}
export async function getMixedZeroReturnStatus(
  parentId: string,
  request: MixedZeroReturnRequest,
  scope: MixedRefundApiScope,
): Promise<MixedZeroReturnStatus> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = mixedZeroReturnRequestSchema.parse(request);
  const { data } = await api.get<unknown>(
    `${zeroPath(parentId)}/operations/${validated.operation_id}`,
    { timeout: 8_000 },
  );
  requireActive(active);
  const result = await validateZeroCompleted(data, parentId, validated, scope);
  requireActive(active);
  return result;
}
export function validateMixedZeroReturnQuote(
  parentId: string,
  input: MixedZeroReturnInput,
  reviewedQuote: MixedRefundQuote,
): void {
  const quote = quoteSchema.parse(reviewedQuote);
  const items = itemsSchema.parse(input.items);
  if (
    quote.parent_sale_id !== parentId ||
    quote.revision_hash !== input.expected_revision_hash ||
    quote.total_amount !== "0.00" ||
    quote.lines.length !== items.length ||
    !items.every((item) => quote.lines.some((line) => matches(item, line)))
  )
    throw new Error("Расчёт не подтверждает возврат без выплаты.");
}
export async function completeMixedZeroReturn(
  parentId: string,
  request: MixedZeroReturnRequest,
  scope: MixedRefundApiScope,
  reviewedQuote?: MixedRefundQuote,
): Promise<MixedZeroReturnPrepared> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = mixedZeroReturnRequestSchema.parse(request);
  if (reviewedQuote) validateMixedZeroReturnQuote(parentId, validated, reviewedQuote);
  const { data } = await api.post<unknown>(`${zeroPath(parentId)}/complete`, validated, {
    timeout: 15_000,
  });
  requireActive(active);
  const result = await validateZeroCompleted(data, parentId, validated, scope, reviewedQuote);
  requireActive(active);
  if (result.state !== "prepared_quarantine") throw new Error("Карантин ещё не подтверждён.");
  return result;
}
