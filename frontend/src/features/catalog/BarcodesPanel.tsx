import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, FormError, Input, Label, Select } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { barcodeLabel, barcodeOptions } from "./labels";
import { useAddBarcode, useCatalogItemQuery, useDeleteBarcode } from "./queries";

const schema = z.object({
  code: z.string().min(1, "Введите код"),
  code_type: z.enum(["ean13", "ean8", "gs1_128", "code128", "qr", "other"]),
});

type FormValues = z.infer<typeof schema>;

export function BarcodesPanel({ itemId }: { itemId: string }): JSX.Element {
  const detail = useCatalogItemQuery(itemId);
  const addMutation = useAddBarcode();
  const deleteMutation = useDeleteBarcode();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: { code: "", code_type: "ean13" },
  });

  const onAdd = form.handleSubmit(async (values) => {
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
    try {
      await addMutation.mutateAsync({
        itemId,
        payload: { code: parsed.data.code.trim(), code_type: parsed.data.code_type },
      });
      form.reset({ code: "", code_type: parsed.data.code_type });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось добавить штрихкод"));
    }
  });

  const onDelete = async (barcodeId: string) => {
    if (!window.confirm("Удалить штрихкод?")) return;
    try {
      await deleteMutation.mutateAsync({ itemId, barcodeId });
    } catch (err) {
      window.alert(describeApiError(err, "Не удалось удалить"));
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-sm font-medium text-slate-700">Штрихкоды</p>
      {detail.isLoading ? (
        <p className="text-sm text-slate-500">Загрузка…</p>
      ) : detail.data?.barcodes.length === 0 ? (
        <p className="text-sm italic text-slate-500">Пока нет штрихкодов</p>
      ) : (
        <ul className="space-y-1">
          {detail.data?.barcodes.map((b) => (
            <li
              key={b.id}
              className="flex items-center justify-between rounded border border-slate-200 bg-white px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <code className="font-mono text-sm">{b.code}</code>
                <Badge tone="neutral">{barcodeLabel[b.code_type]}</Badge>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void onDelete(b.id)}
                isLoading={deleteMutation.isPending}
              >
                Удалить
              </Button>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={onAdd} noValidate className="grid grid-cols-[1fr_140px_auto] gap-2 items-end">
        <div>
          <Label htmlFor="bc_code">Код</Label>
          <Input
            id="bc_code"
            invalid={Boolean(form.formState.errors.code)}
            {...form.register("code")}
          />
          <FormError>{form.formState.errors.code?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="bc_type">Тип</Label>
          <Select id="bc_type" {...form.register("code_type")}>
            {barcodeOptions.map((t) => (
              <option key={t} value={t}>
                {barcodeLabel[t]}
              </option>
            ))}
          </Select>
        </div>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          + Добавить
        </Button>
      </form>
      {topError && <p className="text-sm text-red-600">{topError}</p>}
    </div>
  );
}
