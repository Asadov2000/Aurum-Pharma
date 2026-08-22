import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { dispensingLabel, dispensingOptions, storageLabel, storageOptions } from "./labels";
import { useCreateCatalogItem, useUpdateCatalogItem } from "./queries";
import { type CatalogItem } from "./types";

const optionalText = z.string().trim().max(255, "Не более 255 символов").optional();

const schema = z.object({
  brand_name: z.string().trim().min(1, "Введите название").max(255, "Не более 255 символов"),
  inn: optionalText,
  manufacturer: optionalText,
  form: optionalText,
  dosage: optionalText,
  pack_size: optionalText,
  atx_code: z.string().trim().max(32, "Не более 32 символов").optional(),
  dispensing_type: z.enum(["prescription", "otc", "special"]),
  storage_type: z.enum(["normal", "cold", "frozen"]),
  category: optionalText,
  base_price: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || /^(?:0|[1-9]\d{0,11})(?:[.,]\d{1,2})?$/.test(value),
      "Введите неотрицательную сумму, не более 2 знаков после запятой",
    )
    .optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  item: CatalogItem | null;
  onClose: () => void;
}

export function CatalogItemForm({ item, onClose }: Props): JSX.Element {
  const isEdit = item !== null;
  const create = useCreateCatalogItem();
  const update = useUpdateCatalogItem();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      brand_name: item?.brand_name ?? "",
      inn: item?.inn ?? "",
      manufacturer: item?.manufacturer ?? "",
      form: item?.form ?? "",
      dosage: item?.dosage ?? "",
      pack_size: item?.pack_size ?? "",
      atx_code: item?.atx_code ?? "",
      dispensing_type: item?.dispensing_type ?? "otc",
      storage_type: item?.storage_type ?? "normal",
      category: item?.category ?? "",
      base_price: item?.base_price ?? "",
      is_active: item?.is_active ?? true,
    },
  });

  useEffect(() => {
    form.reset({
      brand_name: item?.brand_name ?? "",
      inn: item?.inn ?? "",
      manufacturer: item?.manufacturer ?? "",
      form: item?.form ?? "",
      dosage: item?.dosage ?? "",
      pack_size: item?.pack_size ?? "",
      atx_code: item?.atx_code ?? "",
      dispensing_type: item?.dispensing_type ?? "otc",
      storage_type: item?.storage_type ?? "normal",
      category: item?.category ?? "",
      base_price: item?.base_price ?? "",
      is_active: item?.is_active ?? true,
    });
  }, [item, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      const firstIssue = parsed.error.issues[0]?.path[0];
      if (typeof firstIssue === "string") form.setFocus(firstIssue as keyof FormValues);
      return;
    }
    setTopError(null);
    const d = parsed.data;
    const nullable = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);
    try {
      if (isEdit && item) {
        await update.mutateAsync({
          id: item.id,
          payload: {
            brand_name: d.brand_name.trim(),
            inn: nullable(d.inn),
            manufacturer: nullable(d.manufacturer),
            form: nullable(d.form),
            dosage: nullable(d.dosage),
            pack_size: nullable(d.pack_size),
            atx_code: nullable(d.atx_code),
            dispensing_type: d.dispensing_type,
            storage_type: d.storage_type,
            category: nullable(d.category),
            base_price: nullable(d.base_price)?.replace(",", ".") ?? null,
            is_active: d.is_active,
          },
        });
      } else {
        await create.mutateAsync({
          brand_name: d.brand_name.trim(),
          inn: nullable(d.inn),
          manufacturer: nullable(d.manufacturer),
          form: nullable(d.form),
          dosage: nullable(d.dosage),
          pack_size: nullable(d.pack_size),
          atx_code: nullable(d.atx_code),
          dispensing_type: d.dispensing_type,
          storage_type: d.storage_type,
          category: nullable(d.category),
          base_price: nullable(d.base_price)?.replace(",", ".") ?? null,
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить позицию"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="brand_name">Торговое название</Label>
          <Input
            id="brand_name"
            invalid={Boolean(form.formState.errors.brand_name)}
            autoComplete="off"
            {...form.register("brand_name")}
          />
          <FormError>{form.formState.errors.brand_name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="inn">МНН</Label>
          <Input id="inn" autoComplete="off" {...form.register("inn")} />
        </div>
        <div>
          <Label htmlFor="manufacturer">Производитель</Label>
          <Input id="manufacturer" autoComplete="off" {...form.register("manufacturer")} />
        </div>
        <div>
          <Label htmlFor="form">Форма</Label>
          <Input id="form" placeholder="таблетки, сироп…" {...form.register("form")} />
        </div>
        <div>
          <Label htmlFor="dosage">Дозировка</Label>
          <Input id="dosage" placeholder="500 мг" {...form.register("dosage")} />
        </div>
        <div>
          <Label htmlFor="pack_size">Упаковка</Label>
          <Input id="pack_size" placeholder="№ 10" {...form.register("pack_size")} />
        </div>
        <div>
          <Label htmlFor="atx_code">ATX-код</Label>
          <Input id="atx_code" {...form.register("atx_code")} />
        </div>
        <div>
          <Label htmlFor="dispensing_type">Отпуск</Label>
          <Select id="dispensing_type" {...form.register("dispensing_type")}>
            {dispensingOptions.map((d) => (
              <option key={d} value={d}>
                {dispensingLabel[d]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="storage_type">Хранение</Label>
          <Select id="storage_type" {...form.register("storage_type")}>
            {storageOptions.map((s) => (
              <option key={s} value={s}>
                {storageLabel[s]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="category">Категория</Label>
          <Input id="category" {...form.register("category")} />
        </div>
        <div>
          <Label htmlFor="base_price">Базовая цена (TJS)</Label>
          <Input
            id="base_price"
            type="text"
            inputMode="decimal"
            placeholder="0.00"
            invalid={Boolean(form.formState.errors.base_price)}
            {...form.register("base_price")}
          />
          <FormError>{form.formState.errors.base_price?.message}</FormError>
        </div>
        {isEdit && (
          <div className="sm:col-span-2">
            <Switch label="Активна" {...form.register("is_active")} />
          </div>
        )}
      </div>
      {topError && (
        <p className="text-sm text-danger" role="alert">
          {topError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          {isEdit ? "Сохранить" : "Создать"}
        </Button>
      </div>
    </form>
  );
}
