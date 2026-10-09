import { z } from "zod";

import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { api } from "@/lib/api";

const uuid = z.string().uuid();
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
export const refundCents = (value: string): bigint => BigInt(value.replace(".", ""));
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
const itemSchema = z.object({ line_id: uuid, sale_format: format, quantity: whole }).strict();
const itemsSchema = z
  .array(itemSchema)
  .min(1)
  .max(200)
  .refine((items) => new Set(items.map((item) => item.line_id)).size === items.length);
export type PhysicalRefundItem = z.infer<typeof itemSchema>;
export const refundReasonSchema = z.enum([
  "dispensing_error",
  "duplicate_sale",
  "pricing_error",
  "quality_issue",
  "damaged_package",
  "customer_cancelled",
  "other",
]);
export const physicalRefundRequestSchema = z
  .object({
    operation_id: uuid.regex(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    ),
    shift_id: uuid,
    items: itemsSchema,
    expected_total_amount: money.refine((value) => refundCents(value) > 0n),
    expected_revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    reason_code: refundReasonSchema.nullable(),
    comment: z.string().max(500).nullable(),
  })
  .strict();
export type PhysicalRefundRequest = z.infer<typeof physicalRefundRequestSchema>;
export type PhysicalRefundInput = Omit<PhysicalRefundRequest, "operation_id">;

const optionsSchema = z
  .object({
    parent_sale_id: uuid,
    branch_id: uuid,
    register_id: uuid,
    original_receipt_number: z.string().min(1).max(100),
    reason_mode: z.enum(["off", "optional", "required", "required_with_text"]),
    lines: z
      .array(
        z
          .object({
            line_id: uuid,
            name: z.string().min(1).max(500),
            unit_label: z.string().min(1).max(30),
            packaging,
            sold_base_units: whole,
            returned_base_units: offset,
            remaining_base_units: offset,
            original_sale_format: format,
            original_retail_unit_price: money,
          })
          .strict()
          .refine(
            (line) => line.returned_base_units + line.remaining_base_units === line.sold_base_units,
          ),
      )
      .max(200),
  })
  .strict()
  .refine(
    (options) => new Set(options.lines.map((line) => line.line_id)).size === options.lines.length,
  );
export type PhysicalRefundOptions = z.infer<typeof optionsSchema>;
const allocationShape = {
  base_units: whole,
  offset,
  gross_amount: money,
  discount_amount: money,
  net_amount: money,
};
const quoteSchema = z
  .object({
    parent_sale_id: uuid,
    revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    total_amount: money,
    currency: z.literal("TJS"),
    lines: z
      .array(
        z
          .object({ ...allocationShape, ...itemSchema.shape })
          .strict()
          .refine(
            (line) =>
              refundCents(line.net_amount) + refundCents(line.discount_amount) ===
              refundCents(line.gross_amount),
          ),
      )
      .min(1)
      .max(200),
  })
  .strict()
  .refine(
    (quote) =>
      quote.lines.reduce((sum, line) => sum + refundCents(line.net_amount), 0n) ===
      refundCents(quote.total_amount),
  );
export type PhysicalRefundQuote = z.infer<typeof quoteSchema>;
const receiptLineSchema = z
  .object({
    ...allocationShape,
    original_line_id: uuid,
    position: z.number().int().positive(),
    name: z.string().min(1).max(500),
    packaging,
    original_sale_format: format,
    original_retail_unit_price: money,
    sale_format: format,
    quantity: whole,
  })
  .strict()
  .refine((line) => {
    const size =
      line.sale_format === "pack"
        ? line.packaging.units_per_pack
        : line.sale_format === "blister"
          ? line.packaging.units_per_blister
          : 1;
    return (
      size !== null &&
      BigInt(line.quantity) * BigInt(size) === BigInt(line.base_units) &&
      refundCents(line.net_amount) + refundCents(line.discount_amount) ===
        refundCents(line.gross_amount)
    );
  });
const receiptSchema = z
  .object({
    schema_version: z.literal(3),
    document_kind: z.literal("physical_cash_refund_receipt"),
    parent_sale_id: uuid,
    parent_receipt_number: z.string().min(1).max(100),
    receipt_number: z.string().min(1).max(100),
    receipt_sequence: z.number().int().positive().safe(),
    completed_at: z.string().datetime({ offset: true }),
    is_test: z.boolean(),
    lines: z.array(receiptLineSchema).min(1).max(200),
    payment_method: z.literal("cash"),
    total_amount: money,
    currency: z.literal("TJS"),
  })
  .strict()
  .refine(
    (receipt) =>
      receipt.lines.every((line, index) => line.position === index + 1) &&
      new Set(receipt.lines.map((line) => line.original_line_id)).size === receipt.lines.length &&
      receipt.lines.reduce((sum, line) => sum + refundCents(line.net_amount), 0n) ===
        refundCents(receipt.total_amount),
  );
const completedSchema = z
  .object({
    state: z.literal("completed"),
    operation_id: uuid,
    parent_sale_id: uuid,
    sale_id: uuid,
    total_amount: money,
    receipt: receiptSchema,
  })
  .strict();
const statusSchema = z.union([
  z.object({ state: z.literal("not_found"), operation_id: uuid, parent_sale_id: uuid }).strict(),
  completedSchema,
]);
export type PhysicalRefundCompleted = z.infer<typeof completedSchema>;
export type PhysicalRefundStatus = z.infer<typeof statusSchema>;

export function parsePhysicalRefundStatus(
  data: unknown,
  parentId: string,
  request: PhysicalRefundRequest,
): PhysicalRefundStatus {
  const status = statusSchema.parse(data);
  if (status.operation_id !== request.operation_id || status.parent_sale_id !== parentId)
    throw new Error("Ответ относится к другой операции возврата.");
  if (status.state === "completed") {
    if (
      status.sale_id === parentId ||
      status.receipt.parent_sale_id !== parentId ||
      status.total_amount !== request.expected_total_amount ||
      status.receipt.total_amount !== status.total_amount ||
      status.receipt.lines.length !== request.items.length ||
      !request.items.every((item) =>
        status.receipt.lines.some(
          (line) =>
            line.original_line_id === item.line_id &&
            line.sale_format === item.sale_format &&
            line.quantity === item.quantity,
        ),
      )
    )
      throw new Error("Сохранённый чек не соответствует подтверждённому возврату.");
  }
  return status;
}

const path = (parentId: string): string =>
  `/sales/${encodeURIComponent(parentId)}/physical-cash-refunds`;
function requireActive(active: () => boolean): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
}
export async function getPhysicalRefundOptions(
  parentId: string,
  branchId: string,
  registerId: string,
  scope: PosStorageScope,
): Promise<PhysicalRefundOptions> {
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
export async function quotePhysicalRefund(
  parentId: string,
  items: PhysicalRefundItem[],
  scope: PosStorageScope,
): Promise<PhysicalRefundQuote> {
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
    !selected.every((item) =>
      quote.lines.some(
        (line) =>
          line.line_id === item.line_id &&
          line.sale_format === item.sale_format &&
          line.quantity === item.quantity,
      ),
    )
  )
    throw new Error("Расчёт относится к другому набору товаров.");
  return quote;
}
export async function getPhysicalRefundStatus(
  parentId: string,
  request: PhysicalRefundRequest,
  scope: PosStorageScope,
): Promise<PhysicalRefundStatus> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = physicalRefundRequestSchema.parse(request);
  const { data } = await api.get<unknown>(
    `${path(parentId)}/operations/${validated.operation_id}`,
    { timeout: 8_000 },
  );
  requireActive(active);
  return parsePhysicalRefundStatus(data, parentId, validated);
}
export async function completePhysicalRefund(
  parentId: string,
  request: PhysicalRefundRequest,
  scope: PosStorageScope,
): Promise<PhysicalRefundCompleted> {
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const validated = physicalRefundRequestSchema.parse(request);
  const { data } = await api.post<unknown>(`${path(parentId)}/complete`, validated, {
    timeout: 15_000,
  });
  requireActive(active);
  const result = parsePhysicalRefundStatus(data, parentId, validated);
  if (result.state !== "completed") throw new Error("Возврат ещё не подтверждён.");
  return result;
}
