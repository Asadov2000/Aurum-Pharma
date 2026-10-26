import {
  mixedElectronicRefundGuard,
  mixedElectronicRefundAuthority,
} from "./mixedElectronicRefundAccess";
import { z } from "zod";

import {
  mixedElectronicRefundSaveRequestSchema,
  parseSavedMixedElectronicRefundReview,
  type MixedElectronicRefundReviewScope,
  type MixedElectronicRefundSaveRequest,
  type SavedMixedElectronicRefundReview,
} from "@/features/sales/mixedElectronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

export interface MixedElectronicRefundActivationScope extends MixedElectronicRefundReviewScope {
  registerId: string;
}
const uuid = z.string().uuid();
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
const timestamp = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)));
function precedes(left: string, right: string): boolean {
  const seconds = (value: string): number => Date.parse(value.replace(/\.\d+/, ""));
  if (seconds(left) !== seconds(right)) return seconds(left) < seconds(right);
  const fraction = (value: string): string => value.match(/\.(\d+)/)?.[1] ?? "";
  const a = fraction(left);
  const b = fraction(right);
  const width = Math.max(a.length, b.length);
  return a.padEnd(width, "0") < b.padEnd(width, "0");
}
const activationSchema = z
  .object({
    state: z.literal("activation_saved"),
    operation_id: mixedElectronicRefundSaveRequestSchema.shape.operation_id,
    parent_sale_id: uuid,
    branch_id: uuid,
    actor_id: uuid,
    register_id: uuid,
    shift_id: uuid,
    activated_at: timestamp,
    review: z.unknown(),
  })
  .strict();
export type SavedMixedElectronicRefundActivation = Omit<
  z.infer<typeof activationSchema>,
  "review"
> & {
  review: SavedMixedElectronicRefundReview;
};

async function requestActivation(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  shiftId?: string,
): Promise<SavedMixedElectronicRefundActivation> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  const shift = shiftId === undefined ? undefined : uuid.parse(shiftId);
  if (validated.request.parent_sale_id !== validatedScope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
  const requireActive = mixedElectronicRefundGuard(validatedScope, shift !== undefined);
  requireActive();
  const base = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/mixed-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}`;
  const config = withAuthSession({ timeout: 8_000 });
  if (shift !== undefined)
    await mixedElectronicRefundAuthority(validatedScope, requireActive, shift);
  requireActive();
  const response =
    shift === undefined
      ? await api.get<unknown>(`${base}/activation`, config)
      : await api.post<unknown>(`${base}/activate`, { shift_id: shift }, config);
  requireActive();
  return parseSavedMixedElectronicRefundActivation(response.data, validatedScope, validated, shift);
}

export function parseSavedMixedElectronicRefundActivation(
  data: unknown,
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  shift?: string,
): SavedMixedElectronicRefundActivation {
  const validatedScope = scopeSchema.parse(scope);
  const validated = mixedElectronicRefundSaveRequestSchema.parse(request);
  if (validated.request.parent_sale_id !== validatedScope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
  const result = activationSchema.parse(data);
  if (
    result.operation_id !== validated.operation_id ||
    result.parent_sale_id !== validatedScope.parentSaleId ||
    result.branch_id !== validatedScope.branchId ||
    result.actor_id !== validatedScope.userId ||
    result.register_id !== validatedScope.registerId ||
    (shift !== undefined && result.shift_id !== shift)
  )
    throw new Error("Активация относится к другому возврату, сотруднику, кассе или смене.");
  const review = parseSavedMixedElectronicRefundReview(result.review, validatedScope, validated);
  const savedAt = timestamp.parse(review.saved_at);
  if (precedes(result.activated_at, savedAt))
    throw new Error("Активация предшествует сохранению выбранного review.");
  return { ...result, review };
}

export async function activateMixedElectronicRefundReview(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
  shiftId: string,
): Promise<SavedMixedElectronicRefundActivation> {
  // Validate even an absent runtime argument: POST must never silently become GET.
  return requestActivation(scope, request, uuid.parse(shiftId));
}

export function readMixedElectronicRefundActivation(
  scope: MixedElectronicRefundActivationScope,
  request: MixedElectronicRefundSaveRequest,
): Promise<SavedMixedElectronicRefundActivation> {
  return requestActivation(scope, request);
}
