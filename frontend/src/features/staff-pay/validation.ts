import { z } from "zod";

export const payAmount = z
  .string()
  .trim()
  .transform((value) => value.replace(",", "."))
  .pipe(
    z
      .string()
      .regex(/^\d{1,12}(?:\.\d{1,2})?$/, "Укажите сумму с точностью до двух знаков после запятой"),
  )
  .transform((value) => value.replace(/^0+(?=\d)/, ""));
export const payDateSchema = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "Выберите дату")
  .refine((value) => {
    const date = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }, "Укажите существующую дату");
export const payFormSchema = z.object({
  amount: payAmount,
  period_from: payDateSchema,
  period_to: payDateSchema,
  paid_on: payDateSchema,
  kind: z.enum(["payment", "advance"]),
  note: z.string().trim().max(1000, "Не более 1000 символов"),
});
export type PayFormValues = z.input<typeof payFormSchema>;
