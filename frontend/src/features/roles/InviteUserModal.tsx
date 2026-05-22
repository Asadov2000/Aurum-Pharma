import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import { useInviteUser, useRolesQuery } from "./queries";

const schema = z.object({
  email: z.string().email("Некорректный email"),
  full_name: z.string().min(1, "Введите имя"),
  role_id: z.string().min(1, "Выберите роль"),
  branch_id: z.string(),
  password_required: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function InviteUserModal({ onClose }: { onClose: () => void }): JSX.Element {
  const roles = useRolesQuery();
  const branches = useBranchesQuery(false);
  const invite = useInviteUser();
  const [topError, setTopError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: {
      email: "",
      full_name: "",
      role_id: "",
      branch_id: "",
      password_required: false,
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
      await invite.mutateAsync({
        email: d.email,
        full_name: d.full_name,
        role_id: d.role_id,
        branch_id: d.branch_id === "" ? null : d.branch_id,
        password_required: d.password_required,
      });
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось пригласить пользователя"));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      <div>
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          type="email"
          invalid={Boolean(form.formState.errors.email)}
          {...form.register("email")}
        />
        <FormError>{form.formState.errors.email?.message}</FormError>
      </div>
      <div>
        <Label htmlFor="full_name">Имя</Label>
        <Input
          id="full_name"
          invalid={Boolean(form.formState.errors.full_name)}
          {...form.register("full_name")}
        />
        <FormError>{form.formState.errors.full_name?.message}</FormError>
      </div>
      <div>
        <Label htmlFor="role_id">Роль</Label>
        <Select
          id="role_id"
          invalid={Boolean(form.formState.errors.role_id)}
          {...form.register("role_id")}
        >
          <option value="">— выберите —</option>
          {roles.data
            ?.filter((r) => r.is_active)
            .map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} (уровень {r.level})
              </option>
            ))}
        </Select>
        <FormError>{form.formState.errors.role_id?.message}</FormError>
      </div>
      <div>
        <Label htmlFor="branch_id">Точка (необязательно)</Label>
        <Select id="branch_id" {...form.register("branch_id")}>
          <option value="">— любая —</option>
          {branches.data?.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </Select>
      </div>
      <Switch
        label="Требовать пароль при входе"
        {...form.register("password_required")}
      />
      {topError && <p className="text-sm text-red-600">{topError}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onClose}>
          Отмена
        </Button>
        <Button type="submit" isLoading={form.formState.isSubmitting}>
          Пригласить
        </Button>
      </div>
    </form>
  );
}
