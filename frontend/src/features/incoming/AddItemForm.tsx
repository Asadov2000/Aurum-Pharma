import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { useAddIncomingItem } from "./queries";

const schema = z.object({
  catalog_id: z.string().min(1, "Выберите позицию"),
  batch_number: z.string().optional(),
  manufactured_at: z.string().optional(),
  expires_at: z.string().min(1, "Укажите срок годности"),
  qty: z.string().refine((v) => Number(v) > 0, "Количество должно быть > 0"),
  purchase_price: z.string().refine((v) => Number(v) >= 0, "Цена должна быть ≥ 0"),
  sale_price: z.string().refine((v) => Number(v) >= 0, "Цена должна быть ≥ 0"),
});

type FormValues = z.infer<typeof schema>;

export function AddItemForm({
  documentId,
  onClose,
}: {
  documentId: string;
  onClose: () => void;
}): JSX.Element {
  const addItem = useAddIncomingItem();
  const [topError, setTopError] = useState<string | null>(null);
  const [catalogId, setCatalogId] = useState("");

  const form = useForm<FormValues>({
    defaultValues: {
      catalog_id: "",
      batch_number: "",
      manufactured_at: "",
      expires_at: "",
      qty: "",
      purchase_price: "",
      sale_price: "",
    },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    const merged = { ...values, catalog_id: catalogId };
    const parsed = schema.safeParse(merged);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        if (p === "catalog_id") {
          setTopError(issue.message);
        } else {
          form.setError(p as keyof FormValues, { message: issue.message });
        }
      }
      return;
    }
    setTopError(null);
    const d = parsed.data;
    try {
      await addItem.mutateAsync({
        documentId,
        payload: {
          catalog_id: d.catalog_id,
          batch_number: d.batch_number?.trim() || null,
          manufactured_at: d.manufactured_at || null,
          expires_at: d.expires_at,
          qty: d.qty,
          purchase_price: d.purchase_price,
          sale_price: d.sale_price,
        },
      });
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить позицию"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-3">
      <div>
        <Label htmlFor="incoming-catalog-item">Позиция каталога</Label>
        <CatalogPicker
          id="incoming-catalog-item"
          value={catalogId}
          onChange={(id) => setCatalogId(id)}
          invalid={Boolean(topError && !catalogId)}
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
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
          Добавить
        </Button>
      </div>
    </form>
  );
}
