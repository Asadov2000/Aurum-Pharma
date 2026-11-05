import { z } from "zod";

import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { api } from "@/lib/api";

const uuid = z.string().uuid();
const operationId = uuid.regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const cents = (value: string): bigint => BigInt(value.replace(".", ""));
const revision = z.string().regex(/^[0-9a-f]{64}$/);
const whole = z.number().int().positive().safe().max(99_999_999_999);
const item = z
  .object({ line_id: uuid, sale_format: z.enum(["unit", "blister", "pack"]), quantity: whole })
  .strict();
export const electronicRefundSelectionSchema = z
  .object({
    items: z
      .array(item)
      .min(1)
      .max(200)
      .refine((items) => new Set(items.map((entry) => entry.line_id)).size === items.length),
  })
  .strict();
export type ElectronicRefundSelection = z.infer<typeof electronicRefundSelectionSchema>;
const intentRequest = z
  .object({
    parent_sale_id: uuid,
    selection: electronicRefundSelectionSchema,
    expected_total_amount: money.refine((value) => cents(value) > 0n),
    expected_revision_hash: revision,
  })
  .strict();
export const electronicRefundSaveRequestSchema = z
  .object({ operation_id: operationId, request: intentRequest })
  .strict();
export type ElectronicRefundSaveRequest = z.infer<typeof electronicRefundSaveRequestSchema>;
const scopeSchema = z
  .object({ tenantId: uuid, userId: uuid, parentSaleId: uuid, branchId: uuid })
  .strict();
export interface ElectronicRefundReviewScope extends PosStorageScope {
  parentSaleId: string;
  branchId: string;
}
const line = z
  .object({
    ...item.shape,
    base_units: whole,
    offset: z.number().int().nonnegative().safe(),
    gross_amount: money,
    discount_amount: money,
    net_amount: money,
  })
  .strict()
  .refine(
    (value) => cents(value.gross_amount) === cents(value.discount_amount) + cents(value.net_amount),
  );
const payment = z
  .object({
    original_payment_id: uuid,
    method: z.enum(["cash", "card", "qr"]),
    amount: money.refine((value) => cents(value) > 0n),
    currency: z.literal("TJS"),
  })
  .strict();
const previewSchema = z
  .object({
    state: z.literal("preview"),
    parent_sale_id: uuid,
    branch_id: uuid,
    original_receipt_number: z.string().min(1).max(100),
    revision_hash: revision,
    total_amount: money,
    currency: z.literal("TJS"),
    lines: z.array(line).min(1).max(200),
    payments: z.array(payment).max(10),
  })
  .strict()
  .refine(
    (value) =>
      new Set(value.lines.map((entry) => entry.line_id)).size === value.lines.length &&
      new Set(value.payments.map((entry) => entry.original_payment_id)).size ===
        value.payments.length &&
      value.lines.reduce((sum, entry) => sum + cents(entry.net_amount), 0n) ===
        cents(value.total_amount) &&
      value.payments.reduce((sum, entry) => sum + cents(entry.amount), 0n) ===
        cents(value.total_amount),
  );
export type ElectronicRefundPreview = z.infer<typeof previewSchema>;
const savedSchema = z
  .object({
    state: z.literal("saved_review"),
    operation_id: operationId,
    saved_at: z.string().datetime({ offset: true }),
    request: intentRequest,
    preview: previewSchema,
  })
  .strict();
export type SavedElectronicRefundReview = z.infer<typeof savedSchema>;

function requireActive(active: () => boolean): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
}
function path(scope: ElectronicRefundReviewScope): string {
  return `/sales/${encodeURIComponent(scope.parentSaleId)}/unit-electronic-refunds`;
}
function assertPreview(
  preview: ElectronicRefundPreview,
  scope: ElectronicRefundReviewScope,
  selection: ElectronicRefundSelection,
): void {
  if (
    preview.parent_sale_id !== scope.parentSaleId ||
    preview.branch_id !== scope.branchId ||
    preview.lines.length !== selection.items.length ||
    !selection.items.every((selected) =>
      preview.lines.some(
        (entry) =>
          entry.line_id === selected.line_id &&
          entry.sale_format === selected.sale_format &&
          entry.quantity === selected.quantity,
      ),
    )
  )
    throw new Error("Расчёт относится к другой продаже, точке или набору товаров.");
}
function assertRequestScope(
  request: ElectronicRefundSaveRequest,
  scope: ElectronicRefundReviewScope,
): void {
  if (request.request.parent_sale_id !== scope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
}
export function parseSavedElectronicRefundReview(
  data: unknown,
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): SavedElectronicRefundReview {
  const saved = savedSchema.parse(data);
  const expected = request.request;
  const actual = saved.request;
  assertPreview(saved.preview, scope, expected.selection);
  if (
    saved.operation_id !== request.operation_id ||
    actual.parent_sale_id !== expected.parent_sale_id ||
    actual.expected_total_amount !== expected.expected_total_amount ||
    actual.expected_revision_hash !== expected.expected_revision_hash ||
    saved.preview.total_amount !== expected.expected_total_amount ||
    saved.preview.revision_hash !== expected.expected_revision_hash ||
    !saved.preview.payments.some((part) => part.method !== "cash") ||
    actual.selection.items.length !== expected.selection.items.length ||
    !actual.selection.items.every((entry, index) => {
      const selected = expected.selection.items[index];
      return (
        selected !== undefined &&
        entry.line_id === selected.line_id &&
        entry.sale_format === selected.sale_format &&
        entry.quantity === selected.quantity
      );
    })
  )
    throw new Error("Сохранённый review не соответствует подтверждённому запросу.");
  return saved;
}

export async function quoteElectronicRefundReview(
  scope: ElectronicRefundReviewScope,
  selection: ElectronicRefundSelection,
): Promise<ElectronicRefundPreview> {
  const validatedScope = scopeSchema.parse(scope);
  const active = capturePosSessionGuard(validatedScope);
  requireActive(active);
  const validated = electronicRefundSelectionSchema.parse(selection);
  const { data } = await api.post<unknown>(`${path(validatedScope)}/quote`, validated, {
    timeout: 8_000,
  });
  requireActive(active);
  const preview = previewSchema.parse(data);
  assertPreview(preview, validatedScope, validated);
  return preview;
}
export async function saveElectronicRefundReview(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): Promise<SavedElectronicRefundReview> {
  const validatedScope = scopeSchema.parse(scope);
  const active = capturePosSessionGuard(validatedScope);
  requireActive(active);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  assertRequestScope(validated, validatedScope);
  const { data } = await api.post<unknown>(`${path(validatedScope)}/reviews`, validated, {
    timeout: 8_000,
  });
  requireActive(active);
  return parseSavedElectronicRefundReview(data, validatedScope, validated);
}
export async function readElectronicRefundReview(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): Promise<SavedElectronicRefundReview> {
  const validatedScope = scopeSchema.parse(scope);
  const active = capturePosSessionGuard(validatedScope);
  requireActive(active);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  assertRequestScope(validated, validatedScope);
  const { data } = await api.get<unknown>(
    `${path(validatedScope)}/reviews/${encodeURIComponent(validated.operation_id)}`,
    { timeout: 8_000 },
  );
  requireActive(active);
  return parseSavedElectronicRefundReview(data, validatedScope, validated);
}
