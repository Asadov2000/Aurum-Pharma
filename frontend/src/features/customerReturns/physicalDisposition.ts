import { z } from "zod";

import { capturePosSessionGuard, type PosStorageScope } from "@/features/pos/posStorageScope";
import { api } from "@/lib/api";

import type { PhysicalCustomerReturnItem } from "./types";

export interface PhysicalDispositionScope extends PosStorageScope {
  branchId: string;
}
const uuid = z.string().uuid();
const targetIdentity = { intake_operation_id: uuid, line_id: uuid };
const physicalTargetSchema = z
  .object({ inventory_kind: z.literal("physical_unit"), ...targetIdentity, lot_id: uuid })
  .strict();
export const physicalDispositionTargetSchema = physicalTargetSchema;
export const physicalDispositionReviewedTargetSchema = physicalTargetSchema
  .extend({
    base_units: z.number().int().positive().safe().max(99_999_999_999),
    unit_label: z.enum(["табл.", "капс.", "амп.", "саше", "супп."]),
  })
  .strict();

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
export const physicalDispositionInputSchema = z
  .object({ target: physicalDispositionTargetSchema, ...decisionFields })
  .strict();
export const physicalDispositionRequestSchema = physicalDispositionInputSchema
  .extend({ decision_operation_id: uuid })
  .strict()
  .refine((value) => value.decision_operation_id !== value.target.intake_operation_id);
export const resolvedPhysicalDispositionSchema = z
  .object({
    version: z.literal(1),
    document_kind: z.literal("physical_customer_return_disposition"),
    state: z.literal("resolved"),
    decision_operation_id: uuid,
    tenant_id: uuid,
    branch_id: uuid,
    parent_sale_id: uuid,
    intake_kind: z.enum(["cash_refund", "zero_return"]),
    target: physicalDispositionReviewedTargetSchema,
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
    items: z.array(resolvedPhysicalDispositionSchema),
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

export type PhysicalDispositionTarget = z.infer<typeof physicalDispositionTargetSchema>;
export type PhysicalDispositionReviewedTarget = z.infer<
  typeof physicalDispositionReviewedTargetSchema
>;
export type PhysicalDispositionInput = z.infer<typeof physicalDispositionInputSchema>;
export type PhysicalDispositionRequest = z.infer<typeof physicalDispositionRequestSchema>;
export type ResolvedPhysicalDisposition = z.infer<typeof resolvedPhysicalDispositionSchema>;
export type PhysicalDispositionHistory = z.infer<typeof historySchema>;
export type PhysicalDispositionStatus =
  | ResolvedPhysicalDisposition
  | z.infer<typeof notFoundSchema>;

export function reviewedPhysicalDispositionTarget(
  item: PhysicalCustomerReturnItem,
): PhysicalDispositionReviewedTarget {
  const identity = { intake_operation_id: item.operation_id, line_id: item.line_id };
  return physicalDispositionReviewedTargetSchema.parse({
    ...identity,
    inventory_kind: "physical_unit",
    lot_id: item.lot_id,
    base_units: item.base_units,
    unit_label: item.unit_label,
  });
}
export function physicalDispositionTarget(
  snapshot: PhysicalDispositionReviewedTarget,
): PhysicalDispositionTarget {
  const { intake_operation_id, line_id, inventory_kind, lot_id } = snapshot;
  return { inventory_kind, intake_operation_id, line_id, lot_id };
}
export function physicalDispositionQuantity(snapshot: PhysicalDispositionReviewedTarget): string {
  const checked = physicalDispositionReviewedTargetSchema.parse(snapshot);
  return `${checked.base_units.toLocaleString("ru-RU")} ${checked.unit_label}`;
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
export async function physicalDispositionRequestHash(
  request: PhysicalDispositionRequest,
  tenantId: string,
): Promise<string> {
  const checked = physicalDispositionRequestSchema.parse(request);
  return sha256(
    canonicalJson({
      version: 1,
      domain: "physical_customer_return_disposition",
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
export async function validatePhysicalDispositionResult(
  value: unknown,
  scope: PosStorageScope & { branchId?: string },
  request?: PhysicalDispositionRequest,
  reviewedTarget?: PhysicalDispositionReviewedTarget,
): Promise<ResolvedPhysicalDisposition> {
  const result = resolvedPhysicalDispositionSchema.parse(value);
  const actual = physicalDispositionRequestSchema.parse({
    decision_operation_id: result.decision_operation_id,
    target: physicalDispositionTarget(result.target),
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
    result.request_hash !== (await physicalDispositionRequestHash(actual, result.tenant_id))
  )
    throw new Error("Ответ сервера не соответствует подтверждённому решению.");
  return result;
}
function requireActive(active: () => boolean): void {
  if (!active()) throw new Error("Аккаунт изменился. Откройте решение заново.");
}
function checkedReview(
  request: PhysicalDispositionRequest,
  reviewedTarget: PhysicalDispositionReviewedTarget,
): PhysicalDispositionRequest {
  const checked = physicalDispositionRequestSchema.parse(request);
  const target = physicalDispositionReviewedTargetSchema.parse(reviewedTarget);
  if (canonicalJson(checked.target) !== canonicalJson(physicalDispositionTarget(target)))
    throw new Error("Подтверждённая строка относится к другому возврату.");
  return checked;
}
export async function getPhysicalDispositionStatus(
  request: PhysicalDispositionRequest,
  reviewedTarget: PhysicalDispositionReviewedTarget,
  scope: PhysicalDispositionScope,
): Promise<PhysicalDispositionStatus> {
  scopeSchema.parse(scope);
  const checked = checkedReview(request, reviewedTarget);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.get<unknown>(
    `/customer-returns/physical/dispositions/operations/${checked.decision_operation_id}`,
    { params: { branch_id: scope.branchId } },
  );
  requireActive(active);
  const status = z.union([notFoundSchema, resolvedPhysicalDispositionSchema]).parse(data);
  if (status.state === "resolved") {
    const result = await validatePhysicalDispositionResult(status, scope, checked, reviewedTarget);
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
export async function completePhysicalDisposition(
  request: PhysicalDispositionRequest,
  reviewedTarget: PhysicalDispositionReviewedTarget,
  scope: PhysicalDispositionScope,
): Promise<ResolvedPhysicalDisposition> {
  scopeSchema.parse(scope);
  const checked = checkedReview(request, reviewedTarget);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.post<unknown>("/customer-returns/physical/dispositions", checked);
  requireActive(active);
  const result = await validatePhysicalDispositionResult(data, scope, checked, reviewedTarget);
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
export async function listPhysicalDispositionHistory(
  params: z.input<typeof historyParamsSchema>,
  scope: PosStorageScope,
): Promise<PhysicalDispositionHistory> {
  const checked = historyParamsSchema.parse(params);
  const active = capturePosSessionGuard(scope);
  requireActive(active);
  const { data } = await api.get<unknown>("/customer-returns/physical/dispositions/history", {
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
      validatePhysicalDispositionResult(item, { ...scope, branchId: checked.branch_id }),
    ),
  );
  if (
    new Set(history.items.map((item) => item.decision_operation_id)).size !== history.items.length
  )
    throw new Error("История содержит повторяющиеся решения.");
  requireActive(active);
  return history;
}
