import { useId, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, ConfirmDialog, FormError, Input, Label, Select } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { barcodeLabel, barcodeOptions } from "./labels";
import { useAddBarcode, useCatalogItemQuery, useDeleteBarcode } from "./queries";

const schema = z
  .object({
    code: z.string().trim().min(1, "Введите код").max(255, "Код слишком длинный"),
    code_type: z.enum(["ean13", "ean8", "gs1_128", "code128", "qr", "other"]),
  })
  .superRefine((value, ctx) => {
    const expectedLength = value.code_type === "ean13" ? 13 : value.code_type === "ean8" ? 8 : null;
    if (expectedLength !== null && !new RegExp(`^\\d{${expectedLength}}$`).test(value.code)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["code"],
        message: `Введите ${expectedLength} цифр`,
      });
    }
  });

type FormValues = z.infer<typeof schema>;

export function BarcodesPanel({
  itemId,
  canManage,
}: {
  itemId: string;
  canManage: boolean;
}): JSX.Element {
  const detail = useCatalogItemQuery(itemId);
  const addMutation = useAddBarcode();
  const deleteMutation = useDeleteBarcode();
  const [topError, setTopError] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const fieldId = useId();
  const codeId = `${fieldId}-code`;
  const typeId = `${fieldId}-type`;

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
      form.setFocus("code");
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
    <div className="space-y-3 border-t border-border pt-4">
      <p className="text-sm font-medium text-foreground-secondary">Штрихкоды</p>
      {detail.isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : detail.error && !detail.data ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2">
          <p className="text-sm text-danger" role="alert">
            {describeApiError(detail.error, "Не удалось загрузить штрихкоды")}
          </p>
          <Button variant="secondary" size="sm" onClick={() => void detail.refetch()}>
            Повторить
          </Button>
        </div>
      ) : detail.data?.barcodes.length === 0 ? (
        <p className="text-sm italic text-foreground-muted">Пока нет штрихкодов</p>
      ) : (
        <ul className="space-y-1">
          {detail.data?.barcodes.map((b) => (
            <li
              key={b.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface px-3 py-2"
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <code className="break-all font-mono text-sm">{b.code}</code>
                <Badge tone="neutral">{barcodeLabel[b.code_type]}</Badge>
              </div>
              {canManage && (
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
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        <form
          onSubmit={onAdd}
          noValidate
          className="grid grid-cols-1 items-end gap-2 sm:grid-cols-[minmax(0,1fr)_140px_auto]"
        >
          <div>
            <Label htmlFor={codeId}>Код</Label>
            <Input
              id={codeId}
              invalid={Boolean(form.formState.errors.code)}
              {...form.register("code")}
            />
            <FormError>{form.formState.errors.code?.message}</FormError>
          </div>
          <div>
            <Label htmlFor={typeId}>Тип</Label>
            <Select id={typeId} {...form.register("code_type")}>
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
      )}
      {topError && (
        <p className="text-sm text-danger" role="alert">
          {topError}
        </p>
      )}
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
