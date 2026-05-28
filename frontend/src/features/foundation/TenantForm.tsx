import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Button,
  FormError,
  Input,
  Label,
  Select,
  Textarea,
} from "@/components/ui";

import { describeApiError } from "./errors";
import { useCreateTenant, useUpdateTenant } from "./queries";
import { type Tenant, type TenantStatus } from "./types";

const statusOptions: TenantStatus[] = [
  "setup",
  "trial",
  "active",
  "grace_period",
  "readonly",
  "archived",
];

const createSchema = z.object({
  name: z.string().min(1, "Введите название"),
  contact_email: z.string().email("Некорректный email"),
  legal_name: z.string().optional(),
  inn_or_tin: z.string().optional(),
  registration_number: z.string().optional(),
  contact_phone: z.string().optional(),
  legal_address: z.string().optional(),
});

const updateSchema = createSchema.extend({
  status: z.enum([
    "setup",
    "trial",
    "active",
    "grace_period",
    "readonly",
    "archived",
  ]),
});

type CreateForm = z.infer<typeof createSchema>;
type UpdateForm = z.infer<typeof updateSchema>;

interface Props {
  tenant: Tenant | null;
  onClose: () => void;
}

export function TenantForm({ tenant, onClose }: Props): JSX.Element {
  const isEdit = tenant !== null;
  const createMutation = useCreateTenant();
  const updateMutation = useUpdateTenant();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<UpdateForm>({
    defaultValues: {
      name: tenant?.name ?? "",
      contact_email: tenant?.contact_email ?? "",
      legal_name: tenant?.legal_name ?? "",
      inn_or_tin: tenant?.inn_or_tin ?? "",
      registration_number: tenant?.registration_number ?? "",
      contact_phone: tenant?.contact_phone ?? "",
      legal_address: tenant?.legal_address ?? "",
      status: tenant?.status ?? "setup",
    },
  });

  useEffect(() => {
    form.reset({
      name: tenant?.name ?? "",
      contact_email: tenant?.contact_email ?? "",
      legal_name: tenant?.legal_name ?? "",
      inn_or_tin: tenant?.inn_or_tin ?? "",
      registration_number: tenant?.registration_number ?? "",
      contact_phone: tenant?.contact_phone ?? "",
      legal_address: tenant?.legal_address ?? "",
      status: tenant?.status ?? "setup",
    });
  }, [tenant, form]);

  const onSubmit = form.handleSubmit(async (values) => {
    const schema = isEdit ? updateSchema : createSchema;
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const p = issue.path[0];
        if (typeof p !== "string" || seen.has(p)) continue;
        seen.add(p);
        form.setError(p as keyof UpdateForm, { message: issue.message });
      }
      return;
    }
    setTopError(null);
    const data = parsed.data;
    const trim = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);
    try {
      if (isEdit && tenant) {
        await updateMutation.mutateAsync({
          id: tenant.id,
          payload: {
            name: data.name,
            contact_email: data.contact_email,
            legal_name: trim(data.legal_name),
            inn_or_tin: trim(data.inn_or_tin),
            registration_number: trim(data.registration_number),
            contact_phone: trim(data.contact_phone),
            legal_address: trim(data.legal_address),
            status: (data as UpdateForm).status,
          },
        });
      } else {
        const c: CreateForm = data as CreateForm;
        await createMutation.mutateAsync({
          name: c.name,
          contact_email: c.contact_email,
          legal_name: trim(c.legal_name),
          inn_or_tin: trim(c.inn_or_tin),
          registration_number: trim(c.registration_number),
          contact_phone: trim(c.contact_phone),
          legal_address: trim(c.legal_address),
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить тенанта"));
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
        <div className="col-span-2">
          <Label htmlFor="contact_email">Контактный email</Label>
          <Input
            id="contact_email"
            type="email"
            invalid={Boolean(form.formState.errors.contact_email)}
            {...form.register("contact_email")}
          />
          <FormError>{form.formState.errors.contact_email?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="legal_name">Юр. название</Label>
          <Input id="legal_name" {...form.register("legal_name")} />
        </div>
        <div>
          <Label htmlFor="contact_phone">Телефон</Label>
          <Input id="contact_phone" {...form.register("contact_phone")} />
        </div>
        <div>
          <Label htmlFor="inn_or_tin">ИНН/TIN</Label>
          <Input id="inn_or_tin" {...form.register("inn_or_tin")} />
        </div>
        <div>
          <Label htmlFor="registration_number">Рег. номер</Label>
          <Input id="registration_number" {...form.register("registration_number")} />
        </div>
        <div className="col-span-2">
          <Label htmlFor="legal_address">Юр. адрес</Label>
          <Textarea id="legal_address" {...form.register("legal_address")} />
        </div>
        {isEdit && (
          <div className="col-span-2">
            <Label htmlFor="status">Статус</Label>
            <Select id="status" {...form.register("status")}>
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
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
