import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "@tanstack/react-router";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";
import { useBranchesQuery } from "@/features/foundation/queries";
import { describeApiError } from "@/features/foundation/errors";
import { useSuppliersQuery } from "@/features/suppliers/queries";

import { useCreateIncoming } from "./queries";

const schema = z.object({
  branch_id: z.string().min(1, "Выберите точку"),
  supplier_id: z.string().min(1, "Выберите поставщика"),
  document_date: z.string().min(1, "Укажите дату"),
  document_number: z.string().optional(),
  notes: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export function NewIncomingForm({ onClose }: { onClose: () => void }): JSX.Element {
  const branches = useBranchesQuery(false);
  const suppliers = useSuppliersQuery(false);
  const create = useCreateIncoming();
  const navigate = useNavigate();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      branch_id: "",
      supplier_id: "",
      document_date: new Date().toISOString().slice(0, 10),
      document_number: "",
      notes: "",
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
      const doc = await create.mutateAsync({
        branch_id: d.branch_id,
        supplier_id: d.supplier_id,
        document_date: d.document_date,
        document_number: d.document_number?.trim() || null,
        notes: d.notes?.trim() || null,
      });
      onClose();
      navigate({ to: "/incoming/$id", params: { id: doc.id } });
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось создать приход"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div>
          <Label htmlFor="branch_id">Точка</Label>
          <Select
            id="branch_id"
            invalid={Boolean(form.formState.errors.branch_id)}
            {...form.register("branch_id")}
          >
            <option value="">— выберите —</option>
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
          <Select
            id="supplier_id"
            invalid={Boolean(form.formState.errors.supplier_id)}
            {...form.register("supplier_id")}
          >
            <option value="">— выберите —</option>
            {suppliers.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
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
          <Label htmlFor="document_number">Номер</Label>
          <Input id="document_number" {...form.register("document_number")} />
        </div>
        <div className="col-span-2">
          <Label htmlFor="notes">Комментарий</Label>
          <Textarea id="notes" {...form.register("notes")} />
        </div>
      </div>
      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          Создать черновик
        </Button>
      </div>
    </form>
  );
}
