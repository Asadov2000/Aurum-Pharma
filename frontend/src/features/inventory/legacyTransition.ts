import { z } from "zod";
import { api, withAuthSession } from "@/lib/api";

const uuid = z.string().uuid();
const exact = z.string().regex(/^-?\d+\.\d{3}$/);
export const legacyPackagingSchema = z
  .object({
    version: z.literal(2),
    unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
    units_per_pack: z.number().int().min(2).max(10000),
    units_per_blister: z.number().int().min(2).max(10000).nullable(),
    dispense_step: z.number().int().min(1).max(10000),
  })
  .strict()
  .refine(
    (p) =>
      p.units_per_pack % p.dispense_step === 0 &&
      (p.units_per_blister === null || p.units_per_pack % p.units_per_blister === 0),
    "Размер блистера и шаг отпуска должны делить упаковку без остатка",
  );
const scope = z.object({ tenant_id: uuid, branch_id: uuid, catalog_id: uuid, batch_id: uuid });
const openedPackSchema = z
  .object({
    id: uuid,
    opened_at: z
      .string()
      .datetime({ offset: true })
      .refine((value) => {
        const year = new Date(value).getUTCFullYear();
        return year >= 1 && year <= 9999 && !/\.\d{7}/.test(value);
      }, "Укажите допустимое время вскрытия с часовым поясом"),
    intact_blisters: z.array(z.number().int().positive().max(5000)),
    opened_blisters: z.array(
      z
        .object({
          number: z.number().int().positive().max(5000),
          remaining_units: z.number().int().positive().max(9999),
        })
        .strict(),
    ),
    loose_units: z.number().int().min(0).max(10000),
  })
  .strict();
const countFields = z
  .object({
    packaging: legacyPackagingSchema,
    closed_packs: z.number().int().min(0).max(99999999999),
    opened_packs: z.array(openedPackSchema),
  })
  .strict();
function validateCount(count: z.infer<typeof countFields>, ctx: z.RefinementCtx): void {
  const { packaging, opened_packs } = count;
  let total = count.closed_packs * packaging.units_per_pack;
  const ids = new Set<string>();
  for (const [index, pack] of opened_packs.entries()) {
    const size = packaging.units_per_blister;
    const numbers = [...pack.intact_blisters, ...pack.opened_blisters.map((b) => b.number)];
    const remaining =
      size === null
        ? pack.loose_units
        : pack.intact_blisters.length * size +
          pack.opened_blisters.reduce((sum, b) => sum + b.remaining_units, 0);
    if (
      ids.has(pack.id.toLowerCase()) ||
      (size === null
        ? numbers.length > 0
        : pack.loose_units !== 0 ||
          new Set(numbers).size !== numbers.length ||
          numbers.some((n) => n > packaging.units_per_pack / size) ||
          pack.opened_blisters.some((b) => b.remaining_units >= size)) ||
      remaining <= 0 ||
      remaining > packaging.units_per_pack
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["opened_packs", index],
        message: "Проверьте состав и остаток вскрытой упаковки",
      });
    }
    ids.add(pack.id.toLowerCase());
    total += remaining;
  }
  if (!Number.isSafeInteger(total) || total > 99999999999) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Количество превышает допустимый предел",
    });
  }
}
export const legacyCountSchema = countFields.superRefine(validateCount);
const previewSchema = z
  .object({
    source: scope.extend({ legacy_quantity: exact }).strict(),
    observation: scope.merge(countFields).superRefine(validateCount),
    recorded_base_units: exact,
    observed_base_units: z.number().int(),
    discrepancy_base_units: exact,
    fractional_base_units: exact,
    recorded_quantity_representable: z.boolean(),
    comparison: z.enum(["matched", "shortage", "surplus"]),
  })
  .strict();
const recordSchema = z
  .object({ id: uuid, created_at: z.string(), created_by: uuid, preview: previewSchema })
  .strict();
const resultSchema = z
  .object({ command_id: uuid, lot_id: uuid, operation_id: uuid, movement_id: uuid })
  .strict();
export type LegacyCount = z.infer<typeof legacyCountSchema>;
export type LegacyOpenedPack = z.infer<typeof openedPackSchema>;
export type LegacyPreview = z.infer<typeof previewSchema>;
export type LegacyRecord = z.infer<typeof recordSchema>;
export type LegacyResult = z.infer<typeof resultSchema>;
export interface LegacyScope {
  tenant_id: string;
  branch_id: string;
  catalog_id: string;
  batch_id: string;
  actor_id: string;
}
function sameCount(preview: LegacyPreview, count: LegacyCount): boolean {
  const { packaging, closed_packs, opened_packs } = preview.observation;
  const canonical = (value: LegacyCount) => {
    const parsed = legacyCountSchema.parse(value);
    return {
      ...parsed,
      opened_packs: parsed.opened_packs
        .map((pack) => ({
          ...pack,
          id: pack.id.toLowerCase(),
          // Preserve microseconds when comparing equivalent timezone representations.
          opened_at:
            new Date(pack.opened_at).toISOString() +
            (pack.opened_at.match(/\.(\d+)/)?.[1] ?? "").padEnd(6, "0").slice(3),
          intact_blisters: [...pack.intact_blisters].sort((a, b) => a - b),
          opened_blisters: [...pack.opened_blisters].sort((a, b) => a.number - b.number),
        }))
        .sort((a, b) => a.id.localeCompare(b.id)),
    };
  };
  return (
    JSON.stringify(canonical({ packaging, closed_packs, opened_packs })) ===
    JSON.stringify(canonical(count))
  );
}
function validatePreview(preview: LegacyPreview, context: LegacyScope): LegacyPreview {
  for (const value of [preview.source, preview.observation]) {
    if (
      value.tenant_id !== context.tenant_id ||
      value.branch_id !== context.branch_id ||
      value.catalog_id !== context.catalog_id ||
      value.batch_id !== context.batch_id
    ) {
      throw new Error("Ответ относится к другой партии или аптеке");
    }
  }
  return preview;
}
function validateRecord(record: LegacyRecord, context: LegacyScope, id: string): LegacyRecord {
  validatePreview(record.preview, context);
  if (record.id !== id || record.created_by !== context.actor_id)
    throw new Error("Не удалось подтвердить автора пересчёта");
  return record;
}
export async function previewLegacyReconciliation(context: LegacyScope, count: LegacyCount) {
  const payload = legacyCountSchema.parse(count);
  const { data } = await api.post<unknown>(
    `/batches/${context.batch_id}/unit-reconciliation/preview`,
    { ...payload, branch_id: context.branch_id },
    withAuthSession(undefined, true),
  );
  const result = validatePreview(previewSchema.parse(data), context);
  if (!sameCount(result, payload)) {
    throw new Error("Сервер вернул другой пересчёт");
  }
  return result;
}
export async function saveLegacyReconciliation(
  context: LegacyScope,
  count: LegacyCount,
  id: string,
  expected: string,
) {
  const { data } = await api.post<unknown>(
    `/batches/${context.batch_id}/unit-reconciliation/records`,
    {
      ...legacyCountSchema.parse(count),
      branch_id: context.branch_id,
      reconciliation_id: uuid.parse(id),
      expected_legacy_quantity: exact.parse(expected),
    },
    withAuthSession(undefined, true),
  );
  const record = validateRecord(recordSchema.parse(data), context, id);
  if (!sameCount(record.preview, count) || record.preview.source.legacy_quantity !== expected) {
    throw new Error("Сохранённый пересчёт не совпадает с отправленным");
  }
  return record;
}
export async function readLegacyReconciliation(
  context: LegacyScope,
  id: string,
  expected?: { count: LegacyCount; quantity: string },
) {
  const { data } = await api.get<unknown>(
    `/batches/${context.batch_id}/unit-reconciliation/records/${uuid.parse(id)}`,
    withAuthSession({ params: { branch_id: context.branch_id } }, true),
  );
  const record = validateRecord(recordSchema.parse(data), context, id);
  if (
    expected &&
    (!sameCount(record.preview, expected.count) ||
      record.preview.source.legacy_quantity !== expected.quantity)
  ) {
    throw new Error("Сохранённый пересчёт не совпадает с отправленным");
  }
  return record;
}
export async function postLegacyTransition(
  context: LegacyScope,
  recordId: string,
  commandId: string,
) {
  const { data } = await api.post<unknown>(
    `/batches/${context.batch_id}/unit-transition`,
    {
      branch_id: context.branch_id,
      reconciliation_id: uuid.parse(recordId),
      command_id: uuid.parse(commandId),
    },
    withAuthSession(undefined, true),
  );
  const result = resultSchema.parse(data);
  if (result.command_id !== commandId) throw new Error("Не удалось подтвердить код переноса");
  return result;
}
export async function readLegacyTransition(context: LegacyScope, commandId: string) {
  const { data } = await api.get<unknown>(
    `/batches/${context.batch_id}/unit-transition/${uuid.parse(commandId)}`,
    withAuthSession({ params: { branch_id: context.branch_id } }, true),
  );
  const result = resultSchema.parse(data);
  if (result.command_id !== commandId) throw new Error("Не удалось подтвердить код переноса");
  return result;
}
