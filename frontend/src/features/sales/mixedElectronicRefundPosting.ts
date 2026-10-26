import {
  mixedElectronicRefundGuard,
  mixedElectronicRefundAuthority,
} from "./mixedElectronicRefundAccess";
import { z } from "zod";

import { type MixedElectronicRefundActivationScope } from "@/features/sales/mixedElectronicRefundActivation";
import {
  parseMixedElectronicRefundConfirmations,
  type ReconciledMixedElectronicRefund,
} from "@/features/sales/mixedElectronicRefundConfirmations";
import {
  mixedElectronicRefundSaveRequestSchema,
  type MixedElectronicRefundSaveRequest,
} from "@/features/sales/mixedElectronicRefundReview";
import { refundReasonSchema } from "@/features/sales/physicalRefund";
import { api, withAuthSession } from "@/lib/api";

const uuid = z.string().uuid();
const money = z.string().regex(/^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/);
const cents = (value: string): bigint =>
  /^(0|[1-9][0-9]{0,11})\.[0-9]{2}$/.test(value) ? BigInt(value.replace(".", "")) : -1n;
const whole = z.number().int().positive().safe().max(99_999_999_999);
const format = z.enum(["unit", "blister", "pack"]);
const timestamp = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)));
const scopeSchema = z
  .object({
    tenantId: uuid,
    userId: uuid,
    parentSaleId: uuid,
    branchId: uuid,
    registerId: uuid,
    contextCheck: z.custom<() => void>((value) => typeof value === "function").optional(),
  })
  .strict();
export const mixedElectronicRefundPostingRequestSchema = z
  .object({
    shift_id: uuid,
    reason_code: refundReasonSchema.nullable().optional(),
    comment: z
      .string()
      .refine((value) => Array.from(value).length <= 500)
      .nullable()
      .optional(),
  })
  .strict();
export type MixedElectronicRefundPostingRequest = z.infer<
  typeof mixedElectronicRefundPostingRequestSchema
>;
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
    (p) =>
      p.units_per_pack % p.dispense_step === 0 &&
      (p.units_per_blister === null || p.units_per_pack % p.units_per_blister === 0),
  );
const physicalLine = z
  .object({
    inventory_kind: z.literal("physical_unit"),
    original_position: z.number().int().positive().safe(),
    original_line_id: uuid,
    position: z.number().int().positive().safe(),
    name: z
      .string()
      .min(1)
      .refine((value) => Array.from(value).length <= 500),
    packaging,
    original_sale_format: format,
    original_retail_unit_price: money,
    sale_format: format,
    quantity: whole,
    base_units: whole,
    offset: z.number().int().nonnegative().safe(),
    gross_amount: money,
    discount_amount: money,
    net_amount: money,
  })
  .strict()
  .refine((l) => {
    const size =
      l.sale_format === "pack"
        ? l.packaging.units_per_pack
        : l.sale_format === "blister"
          ? l.packaging.units_per_blister
          : 1;
    return (
      size !== null &&
      l.base_units % l.packaging.dispense_step === 0 &&
      BigInt(l.base_units) === BigInt(l.quantity) * BigInt(size) &&
      cents(l.gross_amount) === cents(l.discount_amount) + cents(l.net_amount)
    );
  });
const legacyLine = z
  .object({
    inventory_kind: z.literal("legacy_pack"),
    original_line_id: uuid,
    position: z.number().int().positive().safe(),
    original_position: z.number().int().positive().safe(),
    name: z
      .string()
      .min(1)
      .refine((v) => Array.from(v).length <= 500),
    original_retail_unit_price: money,
    quantity: z
      .string()
      .regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/)
      .refine((v) => /^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/.test(v) && BigInt(v.replace(".", "")) > 0n),
    offset: z.string().regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/),
    gross_amount: money,
    discount_amount: money,
    net_amount: money,
  })
  .strict()
  .refine((l) => cents(l.gross_amount) === cents(l.discount_amount) + cents(l.net_amount));
const line = z.union([legacyLine, physicalLine]);
const evidenceText = (limit: number) =>
  z
    .string()
    .min(1)
    .refine(
      (v) =>
        v === v.trim() &&
        Array.from(v).length <= limit &&
        Array.from(v).every((c) => c.charCodeAt(0) >= 32 && c.charCodeAt(0) !== 127),
    );
const payment = z
  .object({
    id: uuid,
    original_payment_id: uuid,
    method: z.enum(["cash", "card", "qr"]),
    amount: money.refine((v) => cents(v) > 0n),
    currency: z.literal("TJS"),
    terminal_id: evidenceText(64).nullable(),
    document_number: evidenceText(128).nullable(),
  })
  .strict()
  .refine((p) =>
    p.method === "cash"
      ? p.terminal_id === null && p.document_number === null
      : p.terminal_id !== null && p.document_number !== null,
  );
const receiptSchema = z
  .object({
    schema_version: z.literal(4),
    document_kind: z.literal("mixed_electronic_refund_receipt"),
    parent_sale_id: uuid,
    parent_receipt_number: z
      .string()
      .min(1)
      .refine((value) => Array.from(value).length <= 100),
    receipt_number: z
      .string()
      .min(1)
      .refine((value) => Array.from(value).length <= 100),
    receipt_sequence: z.number().int().positive().safe(),
    completed_at: timestamp,
    is_test: z.literal(false),
    lines: z.array(line).min(1).max(200),
    payments: z.array(payment).min(1).max(3),
    total_amount: money.refine((v) => cents(v) > 0n),
    currency: z.literal("TJS"),
  })
  .strict();
const postedSchema = z
  .object({
    state: z.literal("posted_refund"),
    operation_id: uuid,
    parent_sale_id: uuid,
    sale_id: uuid,
    tenant_id: uuid,
    branch_id: uuid,
    register_id: uuid,
    shift_id: uuid,
    actor_id: uuid,
    total_amount: money,
    physical_cost_amount: money,
    receipt: receiptSchema,
  })
  .strict();
export type PostedMixedElectronicRefund = z.infer<typeof postedSchema>;

function precedes(left: string, right: string): boolean {
  const seconds = (v: string): number => Date.parse(v.replace(/\.\d+/, ""));
  if (seconds(left) !== seconds(right)) return seconds(left) < seconds(right);
  const fraction = (v: string): string => v.match(/\.(\d+)/)?.[1] ?? "";
  const a = fraction(left),
    b = fraction(right),
    width = Math.max(a.length, b.length);
  return a.padEnd(width, "0") < b.padEnd(width, "0");
}

async function requestPosting(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  evidence: ReconciledMixedElectronicRefund,
  details?: MixedElectronicRefundPostingRequest,
): Promise<PostedMixedElectronicRefund> {
  const s = scopeSchema.parse(scope);
  const r = mixedElectronicRefundSaveRequestSchema.parse(request);
  const e = parseMixedElectronicRefundConfirmations(evidence, s, r, evidence.activation);
  if (e.state !== "confirmed_evidence_only" || e.missing_payment_ids.length !== 0)
    throw new Error("Сначала сохраните полный набор подтверждений возврата.");
  const payload =
    details === undefined ? undefined : mixedElectronicRefundPostingRequestSchema.parse(details);
  const requireActive = mixedElectronicRefundGuard(s, payload !== undefined);
  requireActive();
  const base = `/sales/${encodeURIComponent(s.parentSaleId)}/mixed-electronic-refunds/reviews/${encodeURIComponent(r.operation_id)}`;
  const config = withAuthSession({ timeout: 8_000 });
  if (payload !== undefined)
    await mixedElectronicRefundAuthority(s, requireActive, payload?.shift_id);
  requireActive();
  const response = payload
    ? await api.post<unknown>(`${base}/post`, payload, config)
    : await api.get<unknown>(`${base}/posting`, config);
  requireActive();
  const result = postedSchema.parse(response.data);
  const receipt = result.receipt,
    preview = e.activation.review.preview;
  if (
    result.operation_id !== r.operation_id ||
    result.parent_sale_id !== s.parentSaleId ||
    result.sale_id === s.parentSaleId ||
    result.tenant_id !== s.tenantId ||
    result.branch_id !== s.branchId ||
    result.register_id !== s.registerId ||
    result.actor_id !== s.userId ||
    (payload && result.shift_id !== payload.shift_id) ||
    receipt.parent_sale_id !== s.parentSaleId ||
    receipt.parent_receipt_number !== preview.original_receipt_number ||
    result.total_amount !== preview.total_amount ||
    receipt.total_amount !== result.total_amount ||
    receipt.lines.length !== preview.lines.length ||
    receipt.payments.length !== preview.payments.length ||
    receipt.lines.reduce((sum, l) => sum + cents(l.net_amount), 0n) !==
      cents(result.total_amount) ||
    receipt.payments.reduce((sum, p) => sum + cents(p.amount), 0n) !== cents(result.total_amount) ||
    new Set(receipt.lines.map((l) => l.original_line_id)).size !== receipt.lines.length ||
    new Set(receipt.payments.map((p) => p.id)).size !== receipt.payments.length ||
    new Set(receipt.payments.map((p) => p.original_payment_id)).size !== receipt.payments.length ||
    !receipt.payments.some((p) => p.method !== "cash") ||
    precedes(receipt.completed_at, e.activation.activated_at) ||
    e.confirmations.some((c) => precedes(receipt.completed_at, c.recorded_at))
  )
    throw new Error("Проведение или чек не соответствуют выбранному возврату.");
  receipt.lines.forEach((l, index) => {
    const q = preview.lines[index];
    if (
      !q ||
      l.position !== index + 1 ||
      l.original_line_id !== q.line_id ||
      l.inventory_kind !== q.inventory_kind ||
      (index > 0 && l.original_position <= receipt.lines[index - 1]!.original_position) ||
      (l.inventory_kind === "physical_unit" &&
        (q.inventory_kind !== "physical_unit" ||
          l.sale_format !== q.sale_format ||
          l.base_units !== q.base_units)) ||
      l.quantity !== q.quantity ||
      l.offset !== q.offset ||
      l.gross_amount !== q.gross_amount ||
      l.discount_amount !== q.discount_amount ||
      l.net_amount !== q.net_amount
    )
      throw new Error("Строка чека не соответствует сохранённому расчёту.");
  });
  if (
    receipt.payments.some(
      (p, i) => i > 0 && p.original_payment_id <= receipt.payments[i - 1]!.original_payment_id,
    )
  )
    throw new Error("Порядок платежей чека изменился.");
  for (const p of receipt.payments) {
    const original = preview.payments.find((v) => v.original_payment_id === p.original_payment_id);
    const confirmation = e.confirmations.find(
      (v) => v.original_payment_id === p.original_payment_id,
    );
    if (
      !original ||
      p.method !== original.method ||
      p.amount !== original.amount ||
      (p.method !== "cash" &&
        (!confirmation ||
          p.terminal_id !== confirmation.terminal_id ||
          p.document_number !== confirmation.document_number))
    )
      throw new Error("Часть выплаты не соответствует исходному платежу и подтверждению.");
  }
  return result;
}

export async function postMixedElectronicRefund(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  evidence: ReconciledMixedElectronicRefund,
  details: MixedElectronicRefundPostingRequest,
): Promise<PostedMixedElectronicRefund> {
  return requestPosting(
    scope,
    request,
    evidence,
    mixedElectronicRefundPostingRequestSchema.parse(details),
  );
}
export function readMixedElectronicRefundPosting(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  evidence: ReconciledMixedElectronicRefund,
): Promise<PostedMixedElectronicRefund> {
  return requestPosting(scope, request, evidence);
}
