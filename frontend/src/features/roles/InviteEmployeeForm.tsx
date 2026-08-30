import { useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button, FormError, Input, Label, Select } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { type Branch } from "@/features/foundation/types";
import { createOperationId } from "@/lib/operationId";

import { useInviteEmployee } from "./queries";
import { hasUnavailableRolePermissions, isManageableRole } from "./roleAccess";
import { type Role } from "./types";

const schema = z.object({
  full_name: z.string().trim().min(1, "Введите имя сотрудника").max(200, "Не более 200 символов"),
  email: z.string().trim().email("Введите корректный email"),
  phone: z.string().trim().max(50, "Не более 50 символов"),
  role_id: z.string().min(1, "Выберите роль"),
  branch_id: z.string(),
});

type FormValues = z.infer<typeof schema>;

const DEFAULT_VALUES: FormValues = {
  full_name: "",
  email: "",
  phone: "",
  role_id: "",
  branch_id: "",
};

export function InviteEmployeeForm({
  tenantId,
  roles,
  branches,
  onCreated,
  onCancel,
}: {
  tenantId: string;
  roles: Role[];
  branches: Branch[];
  onCreated: (fullName: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const mutation = useInviteEmployee();
  const [topError, setTopError] = useState<string | null>(null);
  const operationId = useRef<string | null>(null);
  if (operationId.current === null) operationId.current = createOperationId();

  const form = useForm<FormValues>({ defaultValues: DEFAULT_VALUES });
  const manageableRoles = roles.filter(
    (role) =>
      role.is_active && !hasUnavailableRolePermissions(role) && isManageableRole(role, tenantId),
  );
  const activeBranches = branches.filter((branch) => branch.is_active);

  const submit = form.handleSubmit(async (values) => {
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string") {
          form.setError(field as keyof FormValues, { message: issue.message });
        }
      }
      return;
    }

    setTopError(null);
    try {
      await mutation.mutateAsync({
        operation_id: operationId.current ?? createOperationId(),
        email: parsed.data.email,
        full_name: parsed.data.full_name,
        phone: parsed.data.phone || null,
        role_id: parsed.data.role_id,
        branch_id: parsed.data.branch_id || null,
        password_required: false,
      });
      onCreated(parsed.data.full_name);
    } catch (error) {
      setTopError(describeApiError(error, "Не удалось создать сотрудника"));
    }
  });

  return (
    <form className="space-y-5" onSubmit={(event) => void submit(event)} noValidate>
      <div className="rounded-md border border-info/25 bg-info-subtle px-3 py-2 text-sm leading-5 text-info-foreground">
        Аккаунт будет привязан только к этой аптеке. Приглашение действует 7 дней; сотрудник сам
        запросит код входа на указанный email.
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="employee-full-name">ФИО сотрудника</Label>
          <Input
            id="employee-full-name"
            autoComplete="name"
            autoFocus
            {...form.register("full_name")}
          />
          <FormError>{form.formState.errors.full_name?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="employee-email">Email для входа</Label>
          <Input
            id="employee-email"
            type="email"
            autoComplete="email"
            placeholder="employee@example.tj"
            {...form.register("email")}
          />
          <FormError>{form.formState.errors.email?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="employee-phone">Телефон (необязательно)</Label>
          <Input
            id="employee-phone"
            type="tel"
            autoComplete="tel"
            placeholder="+992 90 000 00 00"
            {...form.register("phone")}
          />
          <FormError>{form.formState.errors.phone?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="employee-role">Роль</Label>
          <Select id="employee-role" {...form.register("role_id")}>
            <option value="">Выберите роль</option>
            {manageableRoles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.role_id?.message}</FormError>
        </div>
        <div>
          <Label htmlFor="employee-branch">Торговая точка</Label>
          <Select id="employee-branch" {...form.register("branch_id")}>
            <option value="">Все точки аптеки</option>
            {activeBranches.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}
              </option>
            ))}
          </Select>
          <FormError>{form.formState.errors.branch_id?.message}</FormError>
        </div>
      </div>

      {manageableRoles.length === 0 ? (
        <FormError>Сначала создайте доступную роль для сотрудника.</FormError>
      ) : null}
      <FormError>{topError}</FormError>

      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
        <Button type="button" variant="secondary" disabled={mutation.isPending} onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="submit"
          isLoading={mutation.isPending}
          disabled={manageableRoles.length === 0}
        >
          Создать и пригласить
        </Button>
      </div>
    </form>
  );
}
