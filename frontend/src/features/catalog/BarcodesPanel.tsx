import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, ConfirmDialog, FormError, Input, Label, Select } from "@/components/ui";
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
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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

  const confirmDelete = async () => {
    if (!pendingDeleteId) return;
    setDeleteError(null);
    try {
      await deleteMutation.mutateAsync({ itemId, barcodeId: pendingDeleteId });
      setPendingDeleteId(null);
    } catch (err) {
      setDeleteError(describeApiError(err, "Не удалось удалить"));
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-border bg-foreground/[0.03] p-4">
      <p className="text-sm font-medium text-foreground-secondary">Штрихкоды</p>
      {detail.isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : detail.data?.barcodes.length === 0 ? (
        <p className="text-sm italic text-foreground-muted">Пока нет штрихкодов</p>
      ) : (
        <ul className="space-y-1">
          {detail.data?.barcodes.map((b) => (
            <li
              key={b.id}
              className="flex items-center justify-between rounded border border-border bg-surface px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <code className="font-mono text-sm">{b.code}</code>
                <Badge tone="neutral">{barcodeLabel[b.code_type]}</Badge>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setDeleteError(null);
                  setPendingDeleteId(b.id);
                }}
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
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Удалить штрихкод"
        message={
          <>
            Штрихкод будет удалён из карточки товара.
            {deleteError && <span className="mt-2 block text-danger">{deleteError}</span>}
          </>
        }
        confirmLabel="Удалить"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          setPendingDeleteId(null);
          setDeleteError(null);
        }}
      />
    </div>
  );
}
