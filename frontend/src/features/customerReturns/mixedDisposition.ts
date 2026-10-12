import { z } from "zod";

import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { api } from "@/lib/api";

import type { MixedCustomerReturnItem } from "./types";

export interface MixedDispositionScope extends PosStorageScope {
  branchId: string;
}
const uuid = z.string().uuid();
const targetIdentity = { intake_operation_id: uuid, line_id: uuid };
const legacyTargetSchema = z
  .object({ inventory_kind: z.literal("legacy_pack"), ...targetIdentity, batch_id: uuid })
  .strict();
const physicalTargetSchema = z
  .object({ inventory_kind: z.literal("physical_unit"), ...targetIdentity, lot_id: uuid })
  .strict();
export const mixedDispositionTargetSchema = z.union([legacyTargetSchema, physicalTargetSchema]);
export const mixedDispositionReviewedTargetSchema = z.union([
  legacyTargetSchema
    .extend({
      legacy_quantity: z
        .string()
        .regex(/^(0|[1-9][0-9]{0,10})\.[0-9]{3}$/)
        .refine((value) => value !== "0.000"),
      unit_label: z.literal("уп."),
    })
    .strict(),
  physicalTargetSchema
    .extend({
      base_units: z.number().int().positive().safe().max(99_999_999_999),
      unit_label: z.enum(["табл.", "капс.", "амп.", "саше", "супп."]),
    })
    .strict(),
]);

// Match Python str.strip exactly; JavaScript trim also removes U+FEFF and misses U+0085.
function pythonWhitespace(code: number): boolean {
  return (
    (code >= 0x09 && code <= 0x0d) ||
    (code >= 0x1c && code <= 0x20) ||
    (code >= 0x2000 && code <= 0x200a) ||
    [0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000].includes(code)
  );
}
function normalizedComment(value: string): string | null {
  let start = 0;
  let end = value.length;
  while (start < end && pythonWhitespace(value.charCodeAt(start))) start += 1;
  while (end > start && pythonWhitespace(value.charCodeAt(end - 1))) end -= 1;
  return value.slice(start, end) || null;
}
function validUtf8(value: string): boolean {
  if (value.includes("\0")) return false;
  for (const character of value) {
    const code = character.codePointAt(0)!;
    if (code >= 0xd800 && code <= 0xdfff) return false;
  }
  return true;
}
const commentTextSchema = z
  .string()
  .refine(validUtf8, "Комментарий содержит недопустимые символы.");
const commentSchema = commentTextSchema
  .nullable()
  .default(null)
  .transform((value) => (value === null ? null : normalizedComment(value)))
  .refine((value) => value === null || Array.from(value).length <= 2000);
const decisionFields = {
  disposition_type: z.enum(["disposed", "supplier_claim", "regulatory_transfer"]),
  reason_code: z.enum(["damaged", "quality_issue", "wrong_item", "expired", "other"]),
  comment: commentSchema,
};
export const mixedDispositionInputSchema = z
  .object({ target: mixedDispositionTargetSchema, ...decisionFields })
  .strict();
export const mixedDispositionRequestSchema = mixedDispositionInputSchema
  .extend({ decision_operation_id: uuid })
  .strict()
  .refine((value) => value.decision_operation_id !== value.target.intake_operation_id);
export const resolvedMixedDispositionSchema = z
  .object({
    version: z.literal(1),
    document_kind: z.literal("mixed_customer_return_disposition"),
    state: z.literal("resolved"),
    decision_operation_id: uuid,
    tenant_id: uuid,
    branch_id: uuid,
    parent_sale_id: uuid,
    intake_kind: z.enum(["cash_refund", "zero_return"]),
    target: mixedDispositionReviewedTargetSchema,
    ...decisionFields,
    comment: commentTextSchema
      .nullable()
      .refine(
        (value) =>
          value === null ||
          (value === normalizedComment(value) && Array.from(value).length <= 2000),
      ),
    request_hash: z.string().regex(/^[0-9a-f]{64}$/),
    actor_id: uuid,
    resolved_at: z.string().datetime({ offset: true }),
  })
  .strict();
const notFoundSchema = z
  .object({ state: z.literal("not_found"), decision_operation_id: uuid, branch_id: uuid })
  .strict();
const historySchema = z
  .object({
    items: z.array(resolvedMixedDispositionSchema),
    report_timezone: z
      .string()
      .min(1)
      .refine((value) => {
        try {
          new Intl.DateTimeFormat("ru-RU", { timeZone: value });
          return true;
        } catch {
          return false;
        }
      })
      .nullable(),
    total: z.number().int().nonnegative().safe(),
    page: z.number().int().positive().max(2_147_483_647),
    page_size: z.number().int().positive().max(200),
  })
  .strict();
const scopeSchema = z.object({ tenantId: uuid, userId: uuid, branchId: uuid }).strict();

export type MixedDispositionTarget = z.infer<typeof mixedDispositionTargetSchema>;
export type MixedDispositionReviewedTarget = z.infer<typeof mixedDispositionReviewedTargetSchema>;
export type MixedDispositionInput = z.infer<typeof mixedDispositionInputSchema>;
export type MixedDispositionRequest = z.infer<typeof mixedDispositionRequestSchema>;
export type ResolvedMixedDisposition = z.infer<typeof resolvedMixedDispositionSchema>;
export type MixedDispositionHistory = z.infer<typeof historySchema>;
export type MixedDispositionStatus = ResolvedMixedDisposition | z.infer<typeof notFoundSchema>;

export function reviewedMixedDispositionTarget(
  item: MixedCustomerReturnItem,
): MixedDispositionReviewedTarget {
  const identity = { intake_operation_id: item.operation_id, line_id: item.line_id };
  return mixedDispositionReviewedTargetSchema.parse(
    item.inventory_kind === "legacy_pack"
      ? {
          ...identity,
          inventory_kind: item.inventory_kind,
          batch_id: item.batch_id,
          legacy_quantity: item.legacy_quantity,
          unit_label: item.unit_label,
        }
      : {
          ...identity,
          inventory_kind: item.inventory_kind,
          lot_id: item.lot_id,
          base_units: item.base_units,
          unit_label: item.unit_label,
        },
  );
}
export function mixedDispositionTarget(
  snapshot: MixedDispositionReviewedTarget,
): MixedDispositionTarget {
  const { intake_operation_id, line_id, inventory_kind } = snapshot;
  return inventory_kind === "legacy_pack"
    ? { inventory_kind, intake_operation_id, line_id, batch_id: snapshot.batch_id }
    : { inventory_kind, intake_operation_id, line_id, lot_id: snapshot.lot_id };
}
export function mixedDispositionQuantity(snapshot: MixedDispositionReviewedTarget): string {
  const checked = mixedDispositionReviewedTargetSchema.parse(snapshot);
  if (checked.inventory_kind === "physical_unit")
    return `${checked.base_units.toLocaleString("ru-RU")} ${checked.unit_label}`;
  const point = checked.legacy_quantity.indexOf(".");
  const whole = checked.legacy_quantity.slice(0, point);
  const fraction = checked.legacy_quantity.slice(point + 1);
  const decimals = fraction.replace(/0+$/, "");
  return `${BigInt(whole).toLocaleString("ru-RU")}${decimals ? `,${decimals}` : ""} уп.`;
}
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
export async function mixedDispositionRequestHash(
  request: MixedDispositionRequest,
  tenantId: string,
): Promise<string> {
  const checked = mixedDispositionRequestSchema.parse(request);
  return sha256(
    canonicalJson({
      version: 1,
      domain: "mixed_customer_return_disposition",
      tenant_id: uuid.parse(tenantId).toLowerCase(),
      target: Object.fromEntries(
        Object.entries(checked.target).map(([key, value]) => [
          key,
          key === "inventory_kind" ? value : value.toLowerCase(),
        ]),
      ),
      disposition_type: checked.disposition_type,
      reason_code: checked.reason_code,
      comment_hash: checked.comment === null ? null : await sha256(checked.comment),
    }),
  );
}
export async function validateMixedDispositionResult(
  value: unknown,
  scope: PosStorageScope & { branchId?: string },
  request?: MixedDispositionRequest,
  reviewedTarget?: MixedDispositionReviewedTarget,
): Promise<ResolvedMixedDisposition> {
  const result = resolvedMixedDispositionSchema.parse(value);
  const actual = mixedDispositionRequestSchema.parse({
    decision_operation_id: result.decision_operation_id,
    target: mixedDispositionTarget(result.target),
    disposition_type: result.disposition_type,
    reason_code: result.reason_code,
    comment: result.comment,
  });
  if (
    result.tenant_id !== scope.tenantId ||
    (scope.branchId !== undefined && result.branch_id !== scope.branchId) ||
    (request !== undefined && canonicalJson(actual) !== canonicalJson(request)) ||
    (reviewedTarget !== undefined &&
      canonicalJson(result.target) !== canonicalJson(reviewedTarget)) ||
    result.request_hash !== (await mixedDispositionRequestHash(actual, result.tenant_id))
  )
    throw new Error("Ответ сервера не соответствует подтверждённому решению.");
  return result;
}
function requireActive(active: () => boolean): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте решение заново.");
}
function checkedReview(
  request: MixedDispositionRequest,
  reviewedTarget: MixedDispositionReviewedTarget,
): MixedDispositionRequest {
  const checked = mixedDispositionRequestSchema.parse(request);
  const target = mixedDispositionReviewedTargetSchema.parse(reviewedTarget);
  if (canonicalJson(checked.target) !== canonicalJson(mixedDispositionTarget(target)))
    throw new Error("Подтверждённая строка относится к другому возврату.");
  return checked;
}
export async function getMixedDispositionStatus(
  request: MixedDispositionRequest,
  reviewedTarget: MixedDispositionReviewedTarget,
  scope: MixedDispositionScope,
): Promise<MixedDispositionStatus> {
  scopeSchema.parse(scope);
  const checked = checkedReview(request, reviewedTarget);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.get<unknown>(
    `/customer-returns/mixed/dispositions/operations/${checked.decision_operation_id}`,
    { params: { branch_id: scope.branchId } },
  );
  requireActive(active);
  const status = z.union([notFoundSchema, resolvedMixedDispositionSchema]).parse(data);
  if (status.state === "resolved") {
    const result = await validateMixedDispositionResult(status, scope, checked, reviewedTarget);
    requireActive(active);
    return result;
  }
  if (
    status.decision_operation_id !== checked.decision_operation_id ||
    status.branch_id !== scope.branchId
  )
    throw new Error("Ответ сервера относится к другому решению.");
  return status;
}
export async function completeMixedDisposition(
  request: MixedDispositionRequest,
  reviewedTarget: MixedDispositionReviewedTarget,
  scope: MixedDispositionScope,
): Promise<ResolvedMixedDisposition> {
  scopeSchema.parse(scope);
  const checked = checkedReview(request, reviewedTarget);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.post<unknown>("/customer-returns/mixed/dispositions", checked);
  requireActive(active);
  const result = await validateMixedDispositionResult(data, scope, checked, reviewedTarget);
  requireActive(active);
  return result;
}
const historyParamsSchema = z
  .object({
    branch_id: uuid.optional(),
    page: z.number().int().positive().max(2_147_483_647).default(1),
    page_size: z.number().int().positive().max(200).default(50),
  })
  .strict();
export async function listMixedDispositionHistory(
  params: z.input<typeof historyParamsSchema>,
  scope: PosStorageScope,
): Promise<MixedDispositionHistory> {
  const checked = historyParamsSchema.parse(params);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.get<unknown>("/customer-returns/mixed/dispositions", {
    params: checked,
  });
  requireActive(active);
  const history = historySchema.parse(data);
  if (
    history.page !== checked.page ||
    history.page_size !== checked.page_size ||
    history.items.length > history.page_size ||
    history.total < history.items.length ||
    (history.items.length > 0 && history.report_timezone === null)
  )
    throw new Error("Некорректная страница истории решений.");
  await Promise.all(
    history.items.map((item) =>
      validateMixedDispositionResult(item, { ...scope, branchId: checked.branch_id }),
    ),
  );
  if (
    new Set(history.items.map((item) => item.decision_operation_id)).size !== history.items.length
  )
    throw new Error("История содержит повторяющиеся решения.");
  requireActive(active);
  return history;
}
