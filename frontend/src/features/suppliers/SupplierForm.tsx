import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  FormError,
  Input,
  Label,
  Switch,
  Textarea,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { useCreateSupplier, useUpdateSupplier } from "./queries";
import { type Supplier } from "./types";

const schema = z.object({
  name: z.string().min(1, "Введите название"),
  legal_name: z.string().optional(),
  inn_or_tin: z.string().optional(),
  contact_person: z.string().optional(),
  phone: z.string().optional(),
  email: z.string().optional().refine(
    (v) => !v || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v),
    "Некорректный email",
  ),
  address: z.string().optional(),
  notes: z.string().optional(),
  is_active: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  supplier: Supplier | null;
  onClose: () => void;
}

export function SupplierForm({ supplier, onClose }: Props): JSX.Element {
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
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить поставщика"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2">
          <Label htmlFor="name">Название</Label>
          <Input id="name" invalid={Boolean(form.formState.errors.name)} {...form.register("name")} />
          <FormError>{form.formState.errors.name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="legal_name">Юр. название</Label>
          <Input id="legal_name" {...form.register("legal_name")} />
        </div>
        <div>
          <Label htmlFor="inn_or_tin">ИНН/TIN</Label>
          <Input id="inn_or_tin" {...form.register("inn_or_tin")} />
        </div>
        <div>
          <Label htmlFor="contact_person">Контактное лицо</Label>
          <Input id="contact_person" {...form.register("contact_person")} />
        </div>
        <div>
          <Label htmlFor="phone">Телефон</Label>
          <Input id="phone" {...form.register("phone")} />
        </div>
        <div className="col-span-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            invalid={Boolean(form.formState.errors.email)}
            {...form.register("email")}
          />
          <FormError>{form.formState.errors.email?.message}</FormError>
        </div>
        <div className="col-span-2">
          <Label htmlFor="address">Адрес</Label>
          <Textarea id="address" {...form.register("address")} />
        </div>
        <div className="col-span-2">
          <Label htmlFor="notes">Заметки</Label>
          <Textarea id="notes" {...form.register("notes")} />
        </div>
        {isEdit && (
          <div className="col-span-2">
            <Switch label="Активен" {...form.register("is_active")} />
          </div>
        )}
      </div>
      {topError && <p className="text-sm text-danger">{topError}</p>}
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
