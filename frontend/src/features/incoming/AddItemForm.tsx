import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { pharmacyCalendarDate } from "./calendar";
import { useAddIncomingItem, useUpdateIncomingItem } from "./queries";
import { type IncomingItem } from "./types";

const decimalString = (
  emptyMessage: string,
  invalidMessage: string,
  allowZero: boolean,
  maxIntegerDigits: number,
  maxDecimalPlaces: number,
) =>
  z
    .string()
    .trim()
    .min(1, emptyMessage)
    .refine((value) => {
      const normalized = value.replace(",", ".");
      if (!/^\d+(?:\.\d+)?$/.test(normalized)) return false;
      const [integer = "", fraction = ""] = normalized.split(".");
      const significantInteger = integer.replace(/^0+(?=\d)/, "");
      const number = Number(normalized);
      return (
        significantInteger.length <= maxIntegerDigits &&
        fraction.length <= maxDecimalPlaces &&
        Number.isFinite(number) &&
        (allowZero ? number >= 0 : number > 0)
      );
    }, invalidMessage);

const schema = z
  .object({
    catalog_id: z.string().min(1, "Выберите позицию"),
    batch_number: z.string().optional(),
    manufactured_at: z.string().optional(),
    expires_at: z.string().min(1, "Укажите срок годности"),
    qty: decimalString(
      "Укажите количество",
      "Количество: до 11 цифр и 3 знаков после запятой, больше 0",
      false,
      11,
      3,
    ),
    purchase_price: decimalString(
      "Укажите цену закупки",
      "Цена: до 12 цифр и 2 знаков после запятой",
      true,
      12,
      2,
    ),
    sale_price: decimalString(
      "Укажите цену продажи",
      "Цена: до 12 цифр и 2 знаков после запятой",
      true,
      12,
      2,
    ),
  })
  .superRefine((values, context) => {
    if (values.expires_at && values.expires_at <= pharmacyCalendarDate()) {
      context.addIssue({
        code: "custom",
        path: ["expires_at"],
        message: "Срок годности должен быть позже сегодняшней даты",
      });
    }
    if (values.manufactured_at && values.expires_at && values.manufactured_at > values.expires_at) {
      context.addIssue({
        code: "custom",
        path: ["manufactured_at"],
        message: "Дата производства не может быть позже срока годности",
      });
    }
  });

type FormValues = z.infer<typeof schema>;

export function AddItemForm({
  documentId,
  onClose,
  item,
}: {
  documentId: string;
  onClose: () => void;
  item?: IncomingItem;
}): JSX.Element {
  const addItem = useAddIncomingItem();
  const updateItem = useUpdateIncomingItem();
  const [topError, setTopError] = useState<string | null>(null);
  const isEditing = item !== undefined;

  const form = useForm<FormValues>({
    defaultValues: {
      catalog_id: item?.catalog_id ?? "",
      batch_number: item?.batch_number ?? "",
      manufactured_at: item?.manufactured_at ?? "",
      expires_at: item?.expires_at ?? "",
      qty: item?.qty ?? "",
      purchase_price: item?.purchase_price ?? "",
      sale_price: item?.sale_price ?? "",
    },
  });

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
      return;
    }
    setTopError(null);
    const d = parsed.data;
    try {
      const payload = {
        catalog_id: d.catalog_id,
        batch_number: d.batch_number?.trim() || null,
        manufactured_at: d.manufactured_at || null,
        expires_at: d.expires_at,
        qty: d.qty.replace(",", "."),
        purchase_price: d.purchase_price.replace(",", "."),
        sale_price: d.sale_price.replace(",", "."),
      };
      if (item) {
        await updateItem.mutateAsync({ documentId, itemId: item.id, payload });
      } else {
        await addItem.mutateAsync({ documentId, payload });
      }
      onClose();
    } catch (err) {
      setTopError(
        describeApiError(
          err,
          isEditing ? "Не удалось сохранить позицию" : "Не удалось добавить позицию",
        ),
      );
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-3">
      <div>
        <Label htmlFor="incoming-catalog-item">Позиция каталога</Label>
        <Controller
          control={form.control}
          name="catalog_id"
          render={({ field }) => (
            <CatalogPicker
              id="incoming-catalog-item"
              value={field.value}
              initialLabel={item?.catalog_name ?? undefined}
              onChange={(id) => field.onChange(id)}
              invalid={Boolean(form.formState.errors.catalog_id)}
            />
          )}
        />
        <FormError>{form.formState.errors.catalog_id?.message}</FormError>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="batch_number">Номер партии</Label>
          <Input id="batch_number" {...form.register("batch_number")} />
        </div>
        <div>
          <Label htmlFor="qty">Количество</Label>
          <Input
            id="qty"
            type="text"
            inputMode="decimal"
            invalid={Boolean(form.formState.errors.qty)}
            {...form.register("qty")}
          />
          <FormError>{form.formState.errors.qty?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="manufactured_at">Произведена</Label>
          <Input id="manufactured_at" type="date" {...form.register("manufactured_at")} />
          <FormError>{form.formState.errors.manufactured_at?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="expires_at">Срок годности</Label>
          <Input
            id="expires_at"
            type="date"
            invalid={Boolean(form.formState.errors.expires_at)}
            {...form.register("expires_at")}
          />
          <FormError>{form.formState.errors.expires_at?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="purchase_price">Цена закупки</Label>
          <Input
            id="purchase_price"
            type="text"
            inputMode="decimal"
            invalid={Boolean(form.formState.errors.purchase_price)}
            {...form.register("purchase_price")}
          />
          <FormError>{form.formState.errors.purchase_price?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="sale_price">Цена продажи</Label>
          <Input
            id="sale_price"
            type="text"
            inputMode="decimal"
            invalid={Boolean(form.formState.errors.sale_price)}
            {...form.register("sale_price")}
          />
          <FormError>{form.formState.errors.sale_price?.message}</FormError>
        </div>
      </div>
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" size="sm" isLoading={form.formState.isSubmitting}>
          {isEditing ? "Сохранить" : "Добавить"}
        </Button>
      </div>
    </form>
  );
}
