import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Switch, Textarea } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useCreateSupplier, useUpdateSupplier } from "./queries";
import { type Supplier } from "./types";

const schema = z.object({
  name: z.string().trim().min(1, "Введите название").max(200, "Не более 200 символов"),
  legal_name: z.string().max(300, "Не более 300 символов").optional(),
  inn_or_tin: z.string().max(40, "Не более 40 символов").optional(),
  contact_person: z.string().max(200, "Не более 200 символов").optional(),
  phone: z.string().max(50, "Не более 50 символов").optional(),
  email: z
    .string()
    .optional()
    .refine((v) => !v || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v), "Некорректный email"),
  address: z.string().max(500, "Не более 500 символов").optional(),
  notes: z.string().max(2000, "Не более 2000 символов").optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  supplier: Supplier | null;
  onClose: () => void;
  onCancel: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function SupplierForm({ supplier, onClose, onCancel, onDirtyChange }: Props): JSX.Element {
  const isEdit = supplier !== null;
  const create = useCreateSupplier();
  const update = useUpdateSupplier();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      name: supplier?.name ?? "",
      legal_name: supplier?.legal_name ?? "",
      inn_or_tin: supplier?.inn_or_tin ?? "",
      contact_person: supplier?.contact_person ?? "",
      phone: supplier?.phone ?? "",
      email: supplier?.email ?? "",
      address: supplier?.address ?? "",
      notes: supplier?.notes ?? "",
      is_active: supplier?.is_active ?? true,
    },
  });

  useEffect(() => {
    form.reset({
      name: supplier?.name ?? "",
      legal_name: supplier?.legal_name ?? "",
      inn_or_tin: supplier?.inn_or_tin ?? "",
      contact_person: supplier?.contact_person ?? "",
      phone: supplier?.phone ?? "",
      email: supplier?.email ?? "",
      address: supplier?.address ?? "",
      notes: supplier?.notes ?? "",
      is_active: supplier?.is_active ?? true,
    });
  }, [supplier, form]);

  useEffect(() => {
    onDirtyChange(form.formState.isDirty);
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
    const trim = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);
    try {
      if (isEdit && supplier) {
        await update.mutateAsync({
          id: supplier.id,
          payload: {
            name: d.name,
            legal_name: trim(d.legal_name),
            inn_or_tin: trim(d.inn_or_tin),
            contact_person: trim(d.contact_person),
            phone: trim(d.phone),
            email: trim(d.email),
            address: trim(d.address),
            notes: trim(d.notes),
            is_active: d.is_active,
          },
        });
      } else {
        await create.mutateAsync({
          name: d.name,
          legal_name: trim(d.legal_name),
          inn_or_tin: trim(d.inn_or_tin),
          contact_person: trim(d.contact_person),
          phone: trim(d.phone),
          email: trim(d.email),
          address: trim(d.address),
          notes: trim(d.notes),
        });
      }
      onDirtyChange(false);
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить поставщика"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="name">Краткое название</Label>
          <Input
            id="name"
            invalid={Boolean(form.formState.errors.name)}
            {...form.register("name")}
          />
          <FormError>{form.formState.errors.name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="legal_name">Юридическое наименование</Label>
          <Input
            id="legal_name"
            invalid={Boolean(form.formState.errors.legal_name)}
            {...form.register("legal_name")}
          />
          <FormError>{form.formState.errors.legal_name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="inn_or_tin">ИНН поставщика</Label>
          <Input
            id="inn_or_tin"
            invalid={Boolean(form.formState.errors.inn_or_tin)}
            {...form.register("inn_or_tin")}
          />
          <FormError>{form.formState.errors.inn_or_tin?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="contact_person">Контактное лицо</Label>
          <Input
            id="contact_person"
            invalid={Boolean(form.formState.errors.contact_person)}
            {...form.register("contact_person")}
          />
          <FormError>{form.formState.errors.contact_person?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="phone">Телефон</Label>
          <Input
            id="phone"
            type="tel"
            autoComplete="tel"
            invalid={Boolean(form.formState.errors.phone)}
            {...form.register("phone")}
          />
          <FormError>{form.formState.errors.phone?.message}</FormError>
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            invalid={Boolean(form.formState.errors.email)}
            {...form.register("email")}
          />
          <FormError>{form.formState.errors.email?.message}</FormError>
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="address">Адрес</Label>
          <Textarea
            id="address"
            invalid={Boolean(form.formState.errors.address)}
            {...form.register("address")}
          />
          <FormError>{form.formState.errors.address?.message}</FormError>
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="notes">Заметки</Label>
          <Textarea
            id="notes"
            invalid={Boolean(form.formState.errors.notes)}
            {...form.register("notes")}
          />
          <FormError>{form.formState.errors.notes?.message}</FormError>
        </div>
        {isEdit && (
          <div className="sm:col-span-2">
            <Switch label="Разрешить выбирать в новых приходах" {...form.register("is_active")} />
            <p className="mt-1 text-xs text-foreground-muted">
              Отключённый поставщик останется в истории, но его нельзя будет выбрать в новом приходе
              или возврате.
            </p>
          </div>
        )}
      </div>
      {topError && (
        <div
          role="alert"
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm leading-5 text-danger-foreground"
        >
          {topError}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          {isEdit ? "Сохранить изменения" : "Добавить поставщика"}
        </Button>
      </div>
    </form>
  );
}
