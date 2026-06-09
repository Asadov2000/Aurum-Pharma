import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Textarea } from "@/components/ui";

import { describeApiError } from "./errors";
import { OwnerCreatedPanel } from "./OwnerCreatedPanel";
import { useCreateTenant, useCreateTenantOwner, useUpdateTenant } from "./queries";
import { type Tenant, type TenantStatus } from "./types";

const statusOptions: TenantStatus[] = [
  "setup",
  "trial",
  "active",
  "grace_period",
  "readonly",
  "archived",
];

const statusLabel: Record<TenantStatus, string> = {
  setup: "Настройка",
  trial: "Пробный",
  active: "Активен",
  grace_period: "Льготный",
  readonly: "Только чтение",
  archived: "Архив",
};

const tenantShape = {
  name: z.string().min(1, "Введите название"),
  contact_email: z.string().email("Некорректный email"),
  legal_name: z.string().optional(),
  inn_or_tin: z.string().optional(),
  registration_number: z.string().optional(),
  contact_phone: z.string().optional(),
  legal_address: z.string().optional(),
};

const createSchema = z.object({
  ...tenantShape,
  owner_email: z.string().email("Некорректный email владельца"),
  owner_full_name: z.string().min(1, "Введите ФИО владельца"),
});

const updateSchema = z.object({
  ...tenantShape,
  status: z.enum(["setup", "trial", "active", "grace_period", "readonly", "archived"]),
});

interface FormValues {
  name: string;
  contact_email: string;
  legal_name?: string;
  inn_or_tin?: string;
  registration_number?: string;
  contact_phone?: string;
  legal_address?: string;
  status: TenantStatus;
  owner_email: string;
  owner_full_name: string;
}

interface Props {
  tenant: Tenant | null;
  onClose: () => void;
}

const trim = (v: string | undefined) => (v && v.trim() !== "" ? v.trim() : null);

export function TenantForm({ tenant, onClose }: Props): JSX.Element {
  const isEdit = tenant !== null;
  const createMutation = useCreateTenant();
  const updateMutation = useUpdateTenant();
  const ownerMutation = useCreateTenantOwner();
  const [topError, setTopError] = useState<string | null>(null);

  // The pharmacy stays once created, so a failed owner step retries on the SAME
  // tenant instead of making a second pharmacy.
  const [createdTenant, setCreatedTenant] = useState<Tenant | null>(null);
  const [provisioned, setProvisioned] = useState<{ pharmacy: string; ownerEmail: string } | null>(
    null,
  );

  const defaults: FormValues = {
    name: tenant?.name ?? "",
    contact_email: tenant?.contact_email ?? "",
    legal_name: tenant?.legal_name ?? "",
    inn_or_tin: tenant?.inn_or_tin ?? "",
    registration_number: tenant?.registration_number ?? "",
    contact_phone: tenant?.contact_phone ?? "",
    legal_address: tenant?.legal_address ?? "",
    status: tenant?.status ?? "setup",
    owner_email: "",
    owner_full_name: "",
  };
  const form = useForm<FormValues>({ defaultValues: defaults });

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
      owner_email: "",
      owner_full_name: "",
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
        form.setError(p as keyof FormValues, { message: issue.message });
      }
      return;
    }
    setTopError(null);

    if (isEdit && tenant) {
      try {
        await updateMutation.mutateAsync({
          id: tenant.id,
          payload: {
            name: values.name,
            contact_email: values.contact_email,
            legal_name: trim(values.legal_name),
            inn_or_tin: trim(values.inn_or_tin),
            registration_number: trim(values.registration_number),
            contact_phone: trim(values.contact_phone),
            legal_address: trim(values.legal_address),
            status: values.status,
          },
        });
        onClose();
      } catch (err) {
        setTopError(describeApiError(err, "Не удалось сохранить аптеку"));
      }
      return;
    }

    // Create flow: pharmacy first, then owner — both must succeed.
    try {
      let target = createdTenant;
      if (!target) {
        target = await createMutation.mutateAsync({
          name: values.name,
          contact_email: values.contact_email,
          legal_name: trim(values.legal_name),
          inn_or_tin: trim(values.inn_or_tin),
          registration_number: trim(values.registration_number),
          contact_phone: trim(values.contact_phone),
          legal_address: trim(values.legal_address),
        });
        setCreatedTenant(target);
      }
      const owner = await ownerMutation.mutateAsync({
        tenantId: target.id,
        payload: { email: values.owner_email, full_name: values.owner_full_name },
      });
      setProvisioned({ pharmacy: target.name, ownerEmail: owner.email });
    } catch (err) {
      // If the pharmacy was created but the owner failed, createdTenant stays
      // set → the button becomes "Повторить…" and only the owner step re-runs.
      setTopError(describeApiError(err, "Не удалось создать аптеку или владельца"));
    }
  });

  if (provisioned) {
    return <OwnerCreatedPanel info={provisioned} onClose={onClose} />;
  }

  const submitLabel = isEdit
    ? "Сохранить"
    : createdTenant
      ? "Повторить создание владельца"
      : "Создать аптеку и владельца";

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2">
          <Label htmlFor="name">Название</Label>
          <Input
            id="name"
            invalid={Boolean(form.formState.errors.name)}
            disabled={createdTenant !== null}
            {...form.register("name")}
          />
          <FormError>{form.formState.errors.name?.message}</FormError>
        </div>
        <div className="col-span-2">
          <Label htmlFor="contact_email">Контактный email</Label>
          <Input
            id="contact_email"
            type="email"
            invalid={Boolean(form.formState.errors.contact_email)}
            disabled={createdTenant !== null}
            {...form.register("contact_email")}
          />
          <FormError>{form.formState.errors.contact_email?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="legal_name">Юр. название</Label>
          <Input
            id="legal_name"
            disabled={createdTenant !== null}
            {...form.register("legal_name")}
          />
        </div>
        <div>
          <Label htmlFor="contact_phone">Телефон</Label>
          <Input
            id="contact_phone"
            disabled={createdTenant !== null}
            {...form.register("contact_phone")}
          />
        </div>
        <div>
          <Label htmlFor="inn_or_tin">ИНН/TIN</Label>
          <Input
            id="inn_or_tin"
            disabled={createdTenant !== null}
            {...form.register("inn_or_tin")}
          />
        </div>
        <div>
          <Label htmlFor="registration_number">Рег. номер</Label>
          <Input
            id="registration_number"
            disabled={createdTenant !== null}
            {...form.register("registration_number")}
          />
        </div>
        <div className="col-span-2">
          <Label htmlFor="legal_address">Юр. адрес</Label>
          <Textarea
            id="legal_address"
            disabled={createdTenant !== null}
            {...form.register("legal_address")}
          />
        </div>

        {isEdit && (
          <div className="col-span-2">
            <Label htmlFor="status">Статус</Label>
            <Select id="status" {...form.register("status")}>
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {statusLabel[s]}
                </option>
              ))}
            </Select>
          </div>
        )}

        {!isEdit && (
          <div className="col-span-2 space-y-4 rounded-md border border-border bg-foreground/[0.03] p-3">
            <p className="text-sm font-medium text-foreground">Владелец аптеки</p>
            {createdTenant && (
              <p className="text-xs text-warning-foreground">
                Аптека уже создана — осталось создать владельца.
              </p>
            )}
            <div>
              <Label htmlFor="owner_full_name">ФИО владельца</Label>
              <Input
                id="owner_full_name"
                invalid={Boolean(form.formState.errors.owner_full_name)}
                {...form.register("owner_full_name")}
              />
              <FormError>{form.formState.errors.owner_full_name?.message}</FormError>
            </div>
            <div>
              <Label htmlFor="owner_email">Email владельца</Label>
              <Input
                id="owner_email"
                type="email"
                invalid={Boolean(form.formState.errors.owner_email)}
                {...form.register("owner_email")}
              />
              <FormError>{form.formState.errors.owner_email?.message}</FormError>
            </div>
          </div>
        )}
      </div>

      {topError && <p className="text-sm text-danger">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
