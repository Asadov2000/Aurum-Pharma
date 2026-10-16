import { isAxiosError } from "axios";
import { z } from "zod";

import {
  capturePosSessionGuard,
  currentPosStorageScope,
  type PosStorageScope,
} from "@/features/pos/posStorageScope";
import { api, withAuthSession } from "@/lib/api";

import { physicalLotSchema } from "./physicalLots";

const uuid = z.string().uuid();
const count = z.number().int().safe().nonnegative().max(99_999_999_999);
export const physicalWriteoffInteger = z
  .string()
  .regex(/^(0|[1-9]\d*)$/)
  .max(30);
const comment = z
  .string()
  .refine((value) => {
    if (Array.from(value).length > 2000 || value.includes("\0")) return false;
    return Array.from(value).every((character) => {
      const code = character.codePointAt(0)!;
      return code < 0xd800 || code > 0xdfff;
    });
  })
  .nullable();
const blister = z
  .object({ number: count.refine((value) => value > 0), units: count.refine((value) => value > 0) })
  .strict();
const chosenPack = z
  .object({ pack_id: uuid, blisters: z.array(blister), loose_units: count })
  .strict();
export const physicalWriteoffSelectionSchema = z
  .object({
    closed_packs: count,
    opened_packs: z.array(chosenPack),
  })
  .strict()
  .superRefine((value, ctx) => {
    const unique = new Set(value.opened_packs.map((pack) => pack.pack_id.toLowerCase()));
    if (
      (!value.closed_packs && !value.opened_packs.length) ||
      unique.size !== value.opened_packs.length
    )
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Выберите неповторяющиеся физические остатки.",
      });
    for (const pack of value.opened_packs) {
      if (
        (!pack.loose_units && !pack.blisters.length) ||
        (pack.loose_units > 0 && pack.blisters.length > 0) ||
        new Set(pack.blisters.map((item) => item.number)).size !== pack.blisters.length
      )
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Проверьте выбор блистеров и отдельных единиц.",
        });
    }
  });
export const physicalWriteoffInputSchema = z
  .object({
    branch_id: uuid,
    lot_id: uuid,
    reason: z.enum(["expired", "damaged", "spoiled", "theft", "other"]),
    comment,
    selection: physicalWriteoffSelectionSchema,
  })
  .strict();
export const physicalWriteoffRequestSchema = physicalWriteoffInputSchema.extend({
  operation_id: uuid,
});
export const physicalWriteoffResultSchema = physicalWriteoffInputSchema
  .omit({ selection: true })
  .extend({
    operation_id: uuid,
    base_units: physicalWriteoffInteger.refine((value) => BigInt(value) > 0n),
    closed_packs: physicalWriteoffInteger,
    created_at: z.string().datetime({ offset: true }),
  })
  .strict();
const packState = z
  .object({
    intact_blisters: z.array(z.number().int().positive().max(10000)),
    opened_blisters: z.array(
      z
        .object({
          number: z.number().int().positive().max(10000),
          remaining_units: count.refine((value) => value > 0),
        })
        .strict(),
    ),
    loose_units: count,
  })
  .strict();
export const physicalWriteoffStockSchema = z
  .object({
    lot: physicalLotSchema,
    opened_packs: z.array(
      z
        .object({ pack_id: uuid, remaining_units: physicalWriteoffInteger, state: packState })
        .strict(),
    ),
    total: z.number().int().safe().nonnegative(),
    page: z.number().int().positive().max(2147483647),
    page_size: z.number().int().positive().max(200),
  })
  .strict()
  .superRefine((value, ctx) => {
    const packaging = value.lot.packaging;
    const size = packaging.units_per_pack;
    const blisterSize = packaging.units_per_blister;
    const invalid = () =>
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Некорректный состав физических остатков.",
      });
    if (
      !Number.isSafeInteger(size) ||
      size < 2 ||
      size > 10000 ||
      (blisterSize !== null && (blisterSize < 2 || size % blisterSize !== 0)) ||
      size % packaging.dispense_step !== 0
    )
      invalid();
    if (
      new Set(value.opened_packs.map((pack) => pack.pack_id.toLowerCase())).size !==
        value.opened_packs.length ||
      value.opened_packs.length > value.page_size ||
      value.opened_packs.length > value.total
    )
      invalid();
    for (const pack of value.opened_packs) {
      const state = pack.state;
      let units = state.loose_units;
      if (blisterSize === null) {
        if (state.intact_blisters.length || state.opened_blisters.length) invalid();
      } else {
        const numbers = [
          ...state.intact_blisters,
          ...state.opened_blisters.map((item) => item.number),
        ];
        if (
          state.loose_units ||
          new Set(numbers).size !== numbers.length ||
          numbers.some((number) => number > size / blisterSize) ||
          state.opened_blisters.some((item) => item.remaining_units >= blisterSize)
        )
          invalid();
        units =
          state.intact_blisters.length * blisterSize +
          state.opened_blisters.reduce((sum, item) => sum + item.remaining_units, 0);
      }
      if (units < 1 || units > size || BigInt(pack.remaining_units) !== BigInt(units)) invalid();
    }
  });
export type PhysicalWriteoffInput = z.infer<typeof physicalWriteoffInputSchema>;
export type PhysicalWriteoffRequest = z.infer<typeof physicalWriteoffRequestSchema>;
export type PhysicalWriteoffResult = z.infer<typeof physicalWriteoffResultSchema>;
export type PhysicalWriteoffStock = z.infer<typeof physicalWriteoffStockSchema>;
export interface PhysicalWriteoffScope extends PosStorageScope {
  branchId: string;
}

function activeGuard(scope: PosStorageScope): () => void {
  const active = capturePosSessionGuard(scope);
  const check = () => {
    if (!active()) throw new Error("Аккаунт изменился. Откройте списание заново.");
  };
  check();
  return check;
}
export async function getPhysicalWriteoffStock(
  lotId: string,
  branchId: string,
  page = 1,
  signal?: AbortSignal,
): Promise<PhysicalWriteoffStock> {
  uuid.parse(lotId);
  uuid.parse(branchId);
  z.number().int().positive().max(2147483647).parse(page);
  const scope = currentPosStorageScope();
  if (!scope) throw new Error("Войдите в аккаунт сотрудника.");
  const check = activeGuard(scope);
  const { data } = await api.get<unknown>(
    `/batches/physical/${lotId}/write-off-stock`,
    withAuthSession({ params: { branch_id: branchId, page, page_size: 50 }, signal }, true),
  );
  check();
  const result = physicalWriteoffStockSchema.parse(data);
  if (
    result.lot.lot_id !== lotId ||
    result.lot.branch_id !== branchId ||
    result.page !== page ||
    result.page_size !== 50
  )
    throw new Error("Ответ относится к другой партии или странице.");
  return result;
}
export function validatePhysicalWriteoffResult(
  value: unknown,
  request: PhysicalWriteoffRequest,
  expectedBaseUnits: string,
): PhysicalWriteoffResult {
  const result = physicalWriteoffResultSchema.parse(value);
  if (
    result.operation_id !== request.operation_id ||
    result.branch_id !== request.branch_id ||
    result.lot_id !== request.lot_id ||
    result.reason !== request.reason ||
    result.comment !== request.comment ||
    result.closed_packs !== String(request.selection.closed_packs) ||
    result.base_units !== expectedBaseUnits
  )
    throw new Error("Ответ сервера не соответствует подтверждённому списанию.");
  return result;
}
export async function getPhysicalWriteoffStatus(
  scope: PhysicalWriteoffScope,
  request: PhysicalWriteoffRequest,
  expectedBaseUnits: string,
): Promise<PhysicalWriteoffResult | null> {
  const checked = physicalWriteoffRequestSchema.parse(request);
  if (checked.branch_id !== scope.branchId) throw new Error("Списание относится к другой точке.");
  const check = activeGuard(scope);
  try {
    const { data } = await api.get<unknown>(
      `/batches/physical/write-offs/operations/${checked.operation_id}`,
      withAuthSession({ params: { branch_id: scope.branchId } }, true),
    );
    check();
    return validatePhysicalWriteoffResult(data, checked, expectedBaseUnits);
  } catch (error) {
    check();
    if (isAxiosError(error) && error.response?.status === 404) return null;
    throw error;
  }
}
export async function completePhysicalWriteoff(
  scope: PhysicalWriteoffScope,
  request: PhysicalWriteoffRequest,
  expectedBaseUnits: string,
): Promise<PhysicalWriteoffResult> {
  const checked = physicalWriteoffRequestSchema.parse(request);
  if (checked.branch_id !== scope.branchId) throw new Error("Списание относится к другой точке.");
  const check = activeGuard(scope);
  const { data } = await api.post<unknown>(
    "/batches/physical/write-offs",
    checked,
    withAuthSession(undefined, true),
  );
  check();
  return validatePhysicalWriteoffResult(data, checked, expectedBaseUnits);
}
