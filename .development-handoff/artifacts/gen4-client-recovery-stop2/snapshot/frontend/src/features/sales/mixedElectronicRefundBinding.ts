import {
  mixedElectronicRefundGuard,
  mixedElectronicRefundAuthority,
} from "./mixedElectronicRefundAccess";
import { z } from "zod";

import {
  mixedElectronicRefundSaveRequestSchema,
  type MixedElectronicRefundReviewScope,
  type MixedElectronicRefundSaveRequest,
} from "@/features/sales/mixedElectronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

const scopeSchema = z
  .object({
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    parentSaleId: z.string().uuid(),
    branchId: z.string().uuid(),
    registerId: z.string().uuid(),
    contextCheck: z.custom<() => void>((v) => typeof v === "function").optional(),
  })
  .strict();
const timestamp = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)));

// Compare the whole instant, including fractions below millisecond precision.
function precedes(left: string, right: string): boolean {
  const fraction = (value: string): string => value.match(/\.(\d+)/)?.[1] ?? "";
  const seconds = (value: string): number => Date.parse(value.replace(/\.\d+/, ""));
  const leftSeconds = seconds(left);
  const rightSeconds = seconds(right);
  if (leftSeconds !== rightSeconds) return leftSeconds < rightSeconds;
  const a = fraction(left);
  const b = fraction(right);
  const width = Math.max(a.length, b.length);
  return a.padEnd(width, "0") < b.padEnd(width, "0");
}

const bindingSchema = z
  .object({
    state: z.enum(["bound", "cancelled"]),
    operation_id: mixedElectronicRefundSaveRequestSchema.shape.operation_id,
    parent_sale_id: z.string().uuid(),
    review_revision_hash: z.string().regex(/^[0-9a-f]{64}$/),
    bound_at: timestamp.nullable(),
    cancelled_at: timestamp.nullable(),
  })
  .strict()
  .refine((value) => {
    if (value.state === "bound") return value.bound_at !== null && value.cancelled_at === null;
    return (
      value.cancelled_at !== null &&
      (value.bound_at === null || !precedes(value.cancelled_at, value.bound_at))
    );
  });
export type MixedElectronicRefundBinding = z.infer<typeof bindingSchema>;

async function requestBinding(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
  action: "bind" | "cancel-binding" | "binding",
): Promise<MixedElectronicRefundBinding> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  if (validated.request.parent_sale_id !== validatedScope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
  const requireActive = mixedElectronicRefundGuard(validatedScope, action !== "binding");
  requireActive();
  const path = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/mixed-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}/${action}`;
  const config = withAuthSession({ timeout: 8_000 });
  if (action !== "binding") await mixedElectronicRefundAuthority(validatedScope, requireActive);
  const response =
    action === "binding"
      ? await api.get<unknown>(path, config)
      : await api.post<unknown>(path, undefined, config);
  requireActive();
  const result = bindingSchema.parse(response.data);
  if (
    result.operation_id !== validated.operation_id ||
    result.parent_sale_id !== validatedScope.parentSaleId ||
    result.review_revision_hash !== validated.request.expected_revision_hash ||
    (action === "bind" && result.state !== "bound") ||
    (action === "cancel-binding" && result.state !== "cancelled")
  )
    throw new Error("Подготовка возврата не соответствует выбранному review или действию.");
  return result;
}

export function bindMixedElectronicRefundReview(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<MixedElectronicRefundBinding> {
  return requestBinding(scope, request, "bind");
}

export function cancelMixedElectronicRefundBinding(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<MixedElectronicRefundBinding> {
  return requestBinding(scope, request, "cancel-binding");
}

export function readMixedElectronicRefundBinding(
  scope: MixedElectronicRefundReviewScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<MixedElectronicRefundBinding> {
  return requestBinding(scope, request, "binding");
}
