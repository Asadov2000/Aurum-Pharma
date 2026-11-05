import { z } from "zod";

import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import {
  electronicRefundSaveRequestSchema,
  type ElectronicRefundReviewScope,
  type ElectronicRefundSaveRequest,
} from "@/features/sales/electronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

const scopeSchema = z
  .object({
    tenantId: z.string().uuid(),
    userId: z.string().uuid(),
    parentSaleId: z.string().uuid(),
    branchId: z.string().uuid(),
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
    operation_id: electronicRefundSaveRequestSchema.shape.operation_id,
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
export type ElectronicRefundBinding = z.infer<typeof bindingSchema>;

async function requestBinding(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
  action: "bind" | "cancel-binding" | "binding",
): Promise<ElectronicRefundBinding> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  if (validated.request.parent_sale_id !== validatedScope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
  const active = capturePosSessionGuard(validatedScope);
  const requireActive = (): void => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
  };
  requireActive();
  const path = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/unit-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}/${action}`;
  const config = withAuthSession({ timeout: 8_000 });
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

export function bindElectronicRefundReview(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): Promise<ElectronicRefundBinding> {
  return requestBinding(scope, request, "bind");
}

export function cancelElectronicRefundBinding(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): Promise<ElectronicRefundBinding> {
  return requestBinding(scope, request, "cancel-binding");
}

export function readElectronicRefundBinding(
  scope: ElectronicRefundReviewScope,
  request: ElectronicRefundSaveRequest,
): Promise<ElectronicRefundBinding> {
  return requestBinding(scope, request, "binding");
}
