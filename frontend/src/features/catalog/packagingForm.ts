import { z } from "zod";

import { describeQuantity } from "./packaging";
import { type CatalogItemUpdatePayload, type UnitSalePreparation } from "./types";

export const packagingFields = {
  packaging_enabled: z.boolean(),
  packaging_unit: z.enum(["tablet", "capsule", "ampoule", "sachet", "suppository"]),
  packaging_units_per_pack: z.string(),
  packaging_has_blister: z.boolean(),
  packaging_units_per_blister: z.string(),
  packaging_unit_price: z.string(),
};
export type PackagingFormValues = z.infer<z.ZodObject<typeof packagingFields>>;

const integer = z
  .string()
  .trim()
  .refine(
    (value) => /^[1-9]\d{0,4}$/.test(value) && Number(value) >= 2 && Number(value) <= 10_000,
    "Введите целое число от 2 до 10 000",
  );
export const unitPreparationSchema = z
  .object({
    unit: packagingFields.packaging_unit,
    units_per_pack: integer,
    has_blister: z.boolean(),
    units_per_blister: z.string(),
    unit_price: z
      .string()
      .trim()
      .refine(
        (value) => /^(0|[1-9]\d{0,11})([.,]\d{1,2})?$/.test(value) && /[1-9]/.test(value),
        "Введите цену больше нуля, не более 12 цифр до запятой и двух после",
      ),
  })
  .superRefine((values, context) => {
    if (!values.has_blister) return;
    const blister = integer.safeParse(values.units_per_blister);
    const pack = integer.safeParse(values.units_per_pack);
    if (!blister.success) {
      context.addIssue({
        code: "custom",
        path: ["units_per_blister"],
        message: "Укажите целое число от 2 до 10 000",
      });
    } else if (pack.success && Number(pack.data) % Number(blister.data) !== 0) {
      context.addIssue({
        code: "custom",
        path: ["units_per_blister"],
        message:
          "В пачке должно быть целое число блистеров. Проверьте количество в пачке и блистере.",
      });
    }
  });

export function packagingDefaults(
  saved: UnitSalePreparation | null | undefined,
): PackagingFormValues {
  return {
    packaging_enabled: Boolean(saved),
    packaging_unit: saved?.unit ?? "tablet",
    packaging_units_per_pack: saved ? String(saved.units_per_pack) : "",
    packaging_has_blister: saved?.units_per_blister != null,
    packaging_units_per_blister:
      saved?.units_per_blister != null ? String(saved.units_per_blister) : "",
    packaging_unit_price: saved?.unit_price ?? "",
  };
}

function preparationInput(values: PackagingFormValues) {
  return {
    unit: values.packaging_unit,
    units_per_pack: values.packaging_units_per_pack,
    has_blister: values.packaging_has_blister,
    units_per_blister: values.packaging_units_per_blister,
    unit_price: values.packaging_unit_price,
  };
}

export function validatePackaging(values: PackagingFormValues, context: z.RefinementCtx): void {
  if (!values.packaging_enabled) return;
  const parsed = unitPreparationSchema.safeParse(preparationInput(values));
  if (!parsed.success) {
    for (const issue of parsed.error.issues) {
      context.addIssue({ ...issue, path: [`packaging_${String(issue.path[0])}`] });
    }
  }
}

export function packagingPatch(
  values: PackagingFormValues,
  saved: UnitSalePreparation | null | undefined,
): Pick<CatalogItemUpdatePayload, "unit_sale_preparation" | "pack_size"> {
  if (!values.packaging_enabled) return saved ? { unit_sale_preparation: null } : {};
  const parsed = unitPreparationSchema.parse(preparationInput(values));
  const [whole, fraction = ""] = parsed.unit_price.replace(",", ".").split(".");
  const preparation: UnitSalePreparation = {
    version: 1,
    state: "prepared",
    unit: parsed.unit,
    units_per_pack: Number(parsed.units_per_pack),
    units_per_blister: parsed.has_blister ? Number(parsed.units_per_blister) : null,
    dispense_step: 1,
    unit_price: `${whole}.${fraction.padEnd(2, "0")}`,
  };
  if (
    saved &&
    saved.unit === preparation.unit &&
    saved.units_per_pack === preparation.units_per_pack &&
    (saved.units_per_blister ?? null) === preparation.units_per_blister &&
    saved.unit_price === preparation.unit_price
  )
    return {};
  return {
    unit_sale_preparation: preparation,
    pack_size: describeQuantity(preparation.units_per_pack, preparation.unit),
  };
}
