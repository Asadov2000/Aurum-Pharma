import { z } from "zod";

import { capturePosSessionGuard } from "@/features/pos/posStorageScope";
import {
  electronicRefundSaveRequestSchema,
  parseSavedElectronicRefundReview,
  type ElectronicRefundReviewScope,
  type ElectronicRefundSaveRequest,
  type SavedElectronicRefundReview,
} from "@/features/sales/electronicRefundReview";
import { api, withAuthSession } from "@/lib/api";

export interface ElectronicRefundActivationScope extends ElectronicRefundReviewScope {
  registerId: string;
}
const uuid = z.string().uuid();
const scopeSchema = z
  .object({ tenantId: uuid, userId: uuid, parentSaleId: uuid, branchId: uuid, registerId: uuid })
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
    operation_id: electronicRefundSaveRequestSchema.shape.operation_id,
    parent_sale_id: uuid,
    branch_id: uuid,
    actor_id: uuid,
    register_id: uuid,
    shift_id: uuid,
    activated_at: timestamp,
    review: z.unknown(),
  })
  .strict();
export type SavedElectronicRefundActivation = Omit<z.infer<typeof activationSchema>, "review"> & {
  review: SavedElectronicRefundReview;
};

async function requestActivation(
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  shiftId?: string,
): Promise<SavedElectronicRefundActivation> {
  const validatedScope = scopeSchema.parse(scope);
  const validated = electronicRefundSaveRequestSchema.parse(request);
  const shift = shiftId === undefined ? undefined : uuid.parse(shiftId);
  if (validated.request.parent_sale_id !== validatedScope.parentSaleId)
    throw new Error("Возврат относится к другой продаже.");
  const active = capturePosSessionGuard(validatedScope);
  const requireActive = (): void => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте возврат заново.");
  };
  requireActive();
  const base = `/sales/${encodeURIComponent(validatedScope.parentSaleId)}/unit-electronic-refunds/reviews/${encodeURIComponent(validated.operation_id)}`;
  const config = withAuthSession({ timeout: 8_000 });
  const response =
    shift === undefined
      ? await api.get<unknown>(`${base}/activation`, config)
      : await api.post<unknown>(`${base}/activate`, { shift_id: shift }, config);
  requireActive();
  return parseSavedElectronicRefundActivation(response.data, validatedScope, validated, shift);
}

export function parseSavedElectronicRefundActivation(
  data: unknown,
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  shift?: string,
): SavedElectronicRefundActivation {
  const validatedScope = scopeSchema.parse(scope);
  const validated = electronicRefundSaveRequestSchema.parse(request);
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
  const review = parseSavedElectronicRefundReview(result.review, validatedScope, validated);
  const savedAt = timestamp.parse(review.saved_at);
  if (precedes(result.activated_at, savedAt))
    throw new Error("Активация предшествует сохранению выбранного review.");
  return { ...result, review };
}

export async function activateElectronicRefundReview(
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
  shiftId: string,
): Promise<SavedElectronicRefundActivation> {
  // Validate even an absent runtime argument: POST must never silently become GET.
  return requestActivation(scope, request, uuid.parse(shiftId));
}

export function readElectronicRefundActivation(
  scope: ElectronicRefundActivationScope,
  request: ElectronicRefundSaveRequest,
): Promise<SavedElectronicRefundActivation> {
  return requestActivation(scope, request);
}
