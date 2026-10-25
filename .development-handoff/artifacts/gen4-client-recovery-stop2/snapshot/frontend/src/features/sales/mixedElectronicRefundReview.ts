import {
  mixedElectronicRefundGuard,
  mixedElectronicRefundAuthority,
} from "./mixedElectronicRefundAccess";
import { z } from "zod";

import { type PosStorageScope } from "@/features/pos/posStorageScope";
import { api, withAuthSession } from "@/lib/api";

const uuid = z.string().uuid();
const operationId = uuid.regex(
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
);
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const cents = (value: string): bigint =>
  /^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/.test(value) ? BigInt(value.replace(".", "")) : -1n;
const revision = z.string().regex(/^[0-9a-f]{64}$/);
const whole = z.number().int().positive().safe().max(99_999_999_999);
const legacyOffset = z.string().regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/);
const legacyQuantity = legacyOffset.refine(
  (v) => /^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/.test(v) && BigInt(v.replace(".", "")) > 0n,
);
const legacyItem = z
  .object({ inventory_kind: z.literal("legacy_pack"), line_id: uuid, quantity: legacyQuantity })
  .strict();
const physicalItem = z
  .object({
    inventory_kind: z.literal("physical_unit"),
    line_id: uuid,
    sale_format: z.enum(["unit", "blister", "pack"]),
    quantity: whole,
  })
  .strict();
const item = z.union([legacyItem, physicalItem]);
export const mixedElectronicRefundSelectionSchema = z
  .object({
    items: z
      .array(item)
      .min(1)
      .max(200)
      .refine((items) => new Set(items.map((entry) => entry.line_id)).size === items.length),
  })
  .strict();
export type MixedElectronicRefundSelection = z.infer<typeof mixedElectronicRefundSelectionSchema>;
const intentRequest = z
  .object({
    parent_sale_id: operationId,
    selection: mixedElectronicRefundSelectionSchema,
    expected_total_amount: money.refine((value) => cents(value) > 0n),
    expected_revision_hash: revision,
  })
  .strict();
export const mixedElectronicRefundSaveRequestSchema = z
  .object({ operation_id: operationId, request: intentRequest })
  .strict();
export type MixedElectronicRefundSaveRequest = z.infer<
  typeof mixedElectronicRefundSaveRequestSchema
>;
const scopeSchema = z
  .object({
    tenantId: uuid,
    userId: uuid,
    parentSaleId: operationId,
    branchId: uuid,
    registerId: uuid,
    contextCheck: z.custom<() => void>((v) => typeof v === "function").optional(),
  })
  .strict();
export interface MixedElectronicRefundReviewScope extends PosStorageScope {
  parentSaleId: string;
  branchId: string;
  registerId: string;
  contextCheck?: () => void;
}
const amounts = { gross_amount: money, discount_amount: money, net_amount: money };
const line = z
  .union([
    legacyItem.extend({ offset: legacyOffset, ...amounts }).strict(),
    physicalItem
      .extend({ base_units: whole, offset: z.number().int().nonnegative().safe(), ...amounts })
      .strict(),
  ])
  .refine((v) => cents(v.gross_amount) === cents(v.discount_amount) + cents(v.net_amount));
const payment = z
  .object({
    original_payment_id: uuid,
    method: z.enum(["cash", "card", "qr"]),
    amount: money.refine((value) => cents(value) > 0n),
    currency: z.literal("TJS"),
  })
  .strict();
export const mixedElectronicRefundPreviewSchema = z
  .object({
    state: z.literal("preview"),
    parent_sale_id: operationId,
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
export type MixedElectronicRefundPreview = z.infer<typeof mixedElectronicRefundPreviewSchema>;
const savedSchema = z
  .object({
    state: z.literal("saved_review"),
    operation_id: operationId,
    saved_at: z.string().datetime({ offset: true }),
    request: intentRequest,
    preview: mixedElectronicRefundPreviewSchema,
  })
  .strict();
export type SavedMixedElectronicRefundReview = z.infer<typeof savedSchema>;

function path(scope: MixedElectronicRefundReviewScope): string {
  return `/sales/${encodeURIComponent(scope.parentSaleId)}/mixed-electronic-refunds`;
}
export function assertMixedElectronicRefundPreview(
  preview: MixedElectronicRefundPreview,
  scope: MixedElectronicRefundReviewScope,
  selection: MixedElectronicRefundSelection,
): void {
  if (
    preview.parent_sale_id !== scope.parentSaleId ||
    preview.branch_id !== scope.branchId ||
    preview.lines.length !== selection.items.length ||
    !selection.items.every((selected) =>
      preview.lines.some(
        (entry) =>
          entry.line_id === selected.line_id &&
          entry.inventory_kind === selected.inventory_kind &&
          entry.quantity === selected.quantity &&
          (entry.inventory_kind !== "physical_unit" ||
            (selected.inventory_kind === "physical_unit" &&
              entry.sale_format === selected.sale_format)),
      ),
    )
  )
    throw new Error("Расчёт относится к другой продаже, точке или набору товаров.");
}
function assertRequestScope(
  request: MixedElectronicRefundSaveRequest,
  scope: MixedElectronicRefundReviewScope,
): void {
  if (request.request.parent_sale_id !== scope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
}
export function parseSavedMixedElectronicRefundReview(
  data: unknown,
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): SavedMixedElectronicRefundReview {
  scope = scopeSchema.parse(scope);
  request = mixedElectronicRefundSaveRequestSchema.parse(request);
  assertRequestScope(request, scope);
  const saved = savedSchema.parse(data);
  const expected = request.request;
  const actual = saved.request;
  assertMixedElectronicRefundPreview(saved.preview, scope, expected.selection);
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
        entry.inventory_kind === selected.inventory_kind &&
        entry.quantity === selected.quantity &&
        (entry.inventory_kind !== "physical_unit" ||
          (selected.inventory_kind === "physical_unit" &&
            entry.sale_format === selected.sale_format))
      );
    })
  )
    throw new Error("Сохранённый review не соответствует подтверждённому запросу.");
  return saved;
}

export async function quoteMixedElectronicRefundReview(
  scope: MixedElectronicRefundReviewScope,
  selection: MixedElectronicRefundSelection,
): Promise<MixedElectronicRefundPreview> {
  const validatedScope = scopeSchema.parse(scope);
  const check = mixedElectronicRefundGuard(validatedScope);
  check();
  const validated = mixedElectronicRefundSelectionSchema.parse(selection);
  const { data } = await api.post<unknown>(
    `${path(validatedScope)}/quote`,
    validated,
    withAuthSession({ timeout: 8_000 }),
  );
  check();
  const preview = mixedElectronicRefundPreviewSchema.parse(data);
  assertMixedElectronicRefundPreview(preview, validatedScope, validated);
  return preview;
}
export async function saveMixedElectronicRefundReview(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<SavedMixedElectronicRefundReview> {
  const validatedScope = scopeSchema.parse(scope);
  const check = mixedElectronicRefundGuard(validatedScope, true);
  check();
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  assertRequestScope(validated, validatedScope);
  await mixedElectronicRefundAuthority(validatedScope, check);
  const { data } = await api.post<unknown>(
    `${path(validatedScope)}/reviews`,
    validated,
    withAuthSession({ timeout: 8_000 }),
  );
  check();
  return parseSavedMixedElectronicRefundReview(data, validatedScope, validated);
}
export async function readMixedElectronicRefundReview(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<SavedMixedElectronicRefundReview> {
  const validatedScope = scopeSchema.parse(scope);
  const check = mixedElectronicRefundGuard(validatedScope);
  check();
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  assertRequestScope(validated, validatedScope);
  const { data } = await api.get<unknown>(
    `${path(validatedScope)}/reviews/${encodeURIComponent(validated.operation_id)}`,
    withAuthSession({ timeout: 8_000 }),
  );
  check();
  return parseSavedMixedElectronicRefundReview(data, validatedScope, validated);
}
