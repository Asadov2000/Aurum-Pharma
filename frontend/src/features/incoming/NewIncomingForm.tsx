import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "@tanstack/react-router";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { createOperationId } from "@/lib/operationId";
import { useBranchesQuery } from "@/features/foundation/queries";
import { SupplierPicker } from "@/features/suppliers/SupplierPicker";

import { pharmacyCalendarDate } from "./calendar";
import { useCreateIncoming, useUpdateIncoming } from "./queries";
import { type IncomingDocument } from "./types";

const schema = z.object({
  branch_id: z.string().min(1, "Выберите точку"),
  supplier_id: z.string().min(1, "Выберите поставщика"),
  document_date: z.string().min(1, "Укажите дату"),
  document_number: z.string().optional(),
  notes: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export function NewIncomingForm({
  onClose,
  onCancel = onClose,
  onDirtyChange,
  document,
}: {
  onClose: () => void;
  onCancel?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  document?: IncomingDocument;
}): JSX.Element {
  const branches = useBranchesQuery(false);
  const create = useCreateIncoming();
  const update = useUpdateIncoming();
  const navigate = useNavigate();
  const [topError, setTopError] = useState<string | null>(null);
  const [operationId] = useState(createOperationId);
  const isEditing = document !== undefined;

  const form = useForm<FormValues>({
    defaultValues: {
      branch_id: document?.branch_id ?? "",
      supplier_id: document?.supplier_id ?? "",
      document_date: document?.document_date ?? pharmacyCalendarDate(),
      document_number: document?.document_number ?? "",
      notes: document?.notes ?? "",
    },
  });

  useEffect(() => {
    onDirtyChange?.(form.formState.isDirty);
  }, [form.formState.isDirty, onDirtyChange]);

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      let firstInvalidField: keyof FormValues | null = null;
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        const field = p as keyof FormValues;
        firstInvalidField ??= field;
        form.setError(field, { message: issue.message });
      }
      if (firstInvalidField) form.setFocus(firstInvalidField);
      return;
    }
    setTopError(null);
    const d = parsed.data;
    try {
      const payload = {
        operation_id: operationId,
        branch_id: d.branch_id,
        supplier_id: d.supplier_id,
        document_date: d.document_date,
        document_number: d.document_number?.trim() || null,
        notes: d.notes?.trim() || null,
      };
      const saved = document
        ? await update.mutateAsync({
            id: document.id,
            payload: {
              branch_id: payload.branch_id,
              supplier_id: payload.supplier_id,
              document_date: payload.document_date,
              document_number: payload.document_number,
              notes: payload.notes,
            },
          })
        : await create.mutateAsync(payload);
      onDirtyChange?.(false);
      onClose();
      if (!document) navigate({ to: "/incoming/$id", params: { id: saved.id } });
    } catch (err) {
      setTopError(
        describeApiError(
          err,
          isEditing ? "Не удалось сохранить реквизиты" : "Не удалось создать приход",
        ),
      );
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {branches.error && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-3 text-sm text-danger-foreground"
        >
          <p>Не удалось загрузить точки.</p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="mt-2"
            onClick={() => void branches.refetch()}
          >
            Повторить
          </Button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="branch_id">Аптечная точка</Label>
          <Select
            id="branch_id"
            invalid={Boolean(form.formState.errors.branch_id)}
            {...form.register("branch_id")}
          >
            <option value="">{branches.isLoading ? "Загрузка точек…" : "— выберите —"}</option>
            {branches.data?.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.branch_id?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="supplier_id">Поставщик</Label>
          <SupplierPicker
            id="supplier_id"
            value={form.watch("supplier_id")}
            onChange={(supplierId) => {
              form.setValue("supplier_id", supplierId, {
                shouldDirty: true,
                shouldValidate: true,
              });
            }}
            invalid={Boolean(form.formState.errors.supplier_id)}
            placeholder="Название поставщика"
          />
          <FormError>{form.formState.errors.supplier_id?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="document_date">Дата документа</Label>
          <Input
            id="document_date"
            type="date"
            invalid={Boolean(form.formState.errors.document_date)}
            {...form.register("document_date")}
          />
          <FormError>{form.formState.errors.document_date?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="document_number">Номер документа поставщика</Label>
          <Input
            id="document_number"
            placeholder="Например, НК-1042"
            {...form.register("document_number")}
          />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="notes">Комментарий</Label>
          <Textarea id="notes" {...form.register("notes")} />
        </div>
      </div>
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="submit"
          disabled={Boolean(branches.error)}
          isLoading={form.formState.isSubmitting}
        >
          {isEditing ? "Сохранить" : "Создать черновик"}
        </Button>
      </div>
    </form>
  );
}
