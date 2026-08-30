import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label } from "@/components/ui";

import { describeApiError } from "./errors";
import { useCreateTenantMember } from "./queries";

const schema = z.object({
  email: z.string().trim().email("Некорректный email"),
  full_name: z.string().trim().min(1, "Введите ФИО").max(200, "Не более 200 символов"),
  phone: z.string().max(50, "Не более 50 символов"),
});

type FormValues = z.infer<typeof schema>;

interface Props {
  tenantId: string;
  tenantName: string;
  onClose: () => void;
}

export function TenantMemberForm({ tenantId, tenantName, onClose }: Props): JSX.Element {
  const createMember = useCreateTenantMember();
  const [topError, setTopError] = useState<string | null>(null);
  const [createdEmail, setCreatedEmail] = useState<string | null>(null);
  const form = useForm<FormValues>({
    defaultValues: { email: "", full_name: "", phone: "" },
  });

  const onSubmit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      const seen = new Set<string>();
      for (const issue of parsed.error.issues) {
        const path = issue.path[0];
        if (typeof path !== "string" || seen.has(path)) continue;
        seen.add(path);
        form.setError(path as keyof FormValues, { message: issue.message });
      }
      return;
    }

    setTopError(null);
    try {
      await createMember.mutateAsync({
        tenantId,
        payload: {
          email: parsed.data.email,
          full_name: parsed.data.full_name,
          phone: parsed.data.phone.trim() || null,
        },
      });
      setCreatedEmail(parsed.data.email);
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось создать аккаунт сотрудника"));
    }
  });

  if (createdEmail) {
    return (
      <div className="space-y-4" role="status">
        <div>
          <p className="font-medium text-foreground">Аккаунт сотрудника создан</p>
          <p className="mt-1 text-sm text-foreground-secondary">
            {createdEmail} прикреплён к аптеке «{tenantName}».
          </p>
          <p className="mt-2 text-sm text-foreground-secondary">
            Приглашение действует 7 дней. Сообщите сотруднику адрес входа: он сам запросит
            одноразовый код на эту почту. Роли можно настроить заранее, но доступ появится только
            после первого подтверждённого входа.
          </p>
        </div>
        <div className="flex justify-end">
          <Button onClick={onClose}>Закрыть</Button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <p className="text-sm text-foreground-secondary">Аптека: {tenantName}</p>

      <div>
        <Label htmlFor="tenant-member-full-name">ФИО</Label>
        <Input
          id="tenant-member-full-name"
          invalid={Boolean(form.formState.errors.full_name)}
          {...form.register("full_name")}
        />
        <FormError>{form.formState.errors.full_name?.message}</FormError>
      </div>

      <div>
        <Label htmlFor="tenant-member-email">Email</Label>
        <Input
          id="tenant-member-email"
          type="email"
          autoComplete="off"
          invalid={Boolean(form.formState.errors.email)}
          {...form.register("email")}
        />
        <FormError>{form.formState.errors.email?.message}</FormError>
      </div>

      <div>
        <Label htmlFor="tenant-member-phone">Телефон (необязательно)</Label>
        <Input
          id="tenant-member-phone"
          type="tel"
          autoComplete="off"
          invalid={Boolean(form.formState.errors.phone)}
          {...form.register("phone")}
        />
        <FormError>{form.formState.errors.phone?.message}</FormError>
      </div>

      {topError && <p className="text-sm text-danger">{topError}</p>}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          Создать аккаунт
        </Button>
      </div>
    </form>
  );
}
