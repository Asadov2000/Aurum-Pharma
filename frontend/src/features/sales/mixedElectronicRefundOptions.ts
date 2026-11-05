import { z } from "zod";

import { api, withAuthSession } from "@/lib/api";

import { mixedElectronicRefundGuard } from "./mixedElectronicRefundAccess";
import { type MixedElectronicRefundActivationScope } from "./mixedElectronicRefundActivation";
import { optionsSchema, type MixedRefundOptions } from "./mixedRefund";

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

export async function getMixedElectronicRefundOptions(
  scope: MixedElectronicRefundActivationScope,
): Promise<MixedRefundOptions> {
  const validated = scopeSchema.parse(scope);
  const active = mixedElectronicRefundGuard(validated);
  active();
  const { data } = await api.get<unknown>(
    `/sales/${encodeURIComponent(validated.parentSaleId)}/mixed-electronic-refunds/options`,
    withAuthSession({ timeout: 8_000 }),
  );
  active();
  const result = optionsSchema.parse(data);
  if (
    result.parent_sale_id !== validated.parentSaleId ||
    result.branch_id !== validated.branchId ||
    result.register_id !== validated.registerId
  )
    throw new Error("Доступные товары относятся к другой продаже, аптеке или кассе.");
  return result;
}
