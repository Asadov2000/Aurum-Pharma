import { isAxiosError } from "axios";
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
  branch_id: z.string().min(1, "Выберите, к каким точкам дать доступ"),
});

type FormValues = z.infer<typeof schema>;

const DEFAULT_VALUES: FormValues = {
  full_name: "",
  email: "",
  phone: "",
  role_id: "",
  branch_id: "",
};

const ALL_BRANCHES = "__all_branches__";

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
  onCreated: (fullName: string, email: string) => void;
  onCancel: () => void;
}): JSX.Element {
  const mutation = useInviteEmployee();
  const [topError, setTopError] = useState<string | null>(null);
  const [review, setReview] = useState<FormValues | null>(null);
  const operationId = useRef<string | null>(null);
  if (operationId.current === null) operationId.current = createOperationId();

  const form = useForm<FormValues>({ defaultValues: DEFAULT_VALUES });
  const manageableRoles = roles.filter(
    (role) =>
      role.is_active && !hasUnavailableRolePermissions(role) && isManageableRole(role, tenantId),
  );
  const activeBranches = branches.filter((branch) => branch.is_active);

  const submit = form.handleSubmit((values) => {
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
    setReview(parsed.data);
  });

  const createAccess = async () => {
    if (!review) return;
    setTopError(null);
    try {
      await mutation.mutateAsync({
        operation_id: operationId.current ?? createOperationId(),
        email: review.email,
        full_name: review.full_name,
        phone: review.phone || null,
        role_id: review.role_id,
        branch_id: review.branch_id === ALL_BRANCHES ? null : review.branch_id,
        password_required: false,
      });
      onCreated(review.full_name, review.email);
    } catch (error) {
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (status !== undefined && status >= 400 && status < 500) {
        setTopError(describeApiError(error, "Не удалось создать сотрудника"));
        setReview(null);
      } else {
        setTopError(
          "Не удалось получить результат. Повторите создание с этими же данными: второй аккаунт не появится.",
        );
      }
    }
  };

  if (review) {
    const selectedRole = manageableRoles.find((role) => role.id === review.role_id);
    const selectedBranch = activeBranches.find((branch) => branch.id === review.branch_id);
    const accessLabel =
      review.branch_id === ALL_BRANCHES
        ? "Все точки аптеки"
        : (selectedBranch?.name ?? "Выбранная точка");

    return (
      <div className="space-y-5">
        <div>
          <h3 className="text-base font-semibold text-foreground">Проверьте данные</h3>
          <p className="mt-1 text-sm leading-5 text-foreground-secondary">
            Убедитесь, что email указан без ошибки: сотрудник будет использовать его для входа.
          </p>
        </div>
        <dl className="divide-y divide-border rounded-md border border-border bg-background px-4">
          <ReviewRow label="Сотрудник" value={review.full_name} />
          <ReviewRow label="Email для входа" value={review.email} />
          <ReviewRow label="Роль" value={selectedRole?.name ?? "Выбранная роль"} />
          <ReviewRow label="Доступ" value={accessLabel} />
        </dl>
        {review.branch_id === ALL_BRANCHES ? (
          <div className="rounded-md border border-warning/30 bg-warning-subtle px-3 py-2 text-sm leading-5 text-warning-foreground">
            Сотрудник получит доступ ко всем текущим и будущим точкам этой аптеки.
          </div>
        ) : null}
        {topError ? <FormError>{topError}</FormError> : null}
        <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="secondary"
            disabled={mutation.isPending || topError !== null}
            onClick={() => setReview(null)}
          >
            Изменить данные
          </Button>
          <Button type="button" isLoading={mutation.isPending} onClick={() => void createAccess()}>
            {topError ? "Повторить создание" : "Создать доступ"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form className="space-y-5" onSubmit={(event) => void submit(event)} noValidate>
      <div className="rounded-md border border-info/25 bg-info-subtle px-3 py-2 text-sm leading-5 text-info-foreground">
        Аккаунт будет привязан только к этой аптеке. Сотрудник откроет страницу входа, укажет этот
        email и получит код. Доступ нужно активировать в течение 7 дней.
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
          <Label htmlFor="employee-branch">Доступ к торговым точкам</Label>
          <Select id="employee-branch" {...form.register("branch_id")}>
            <option value="">Выберите доступ</option>
            {activeBranches.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}
              </option>
            ))}
            <option value={ALL_BRANCHES}>Все точки аптеки</option>
          </Select>
          <FormError>{form.formState.errors.branch_id?.message}</FormError>
          <p className="mt-1 text-xs leading-5 text-foreground-muted">
            Дополнительные точки можно добавить после создания сотрудника.
          </p>
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
          Проверить данные
        </Button>
      </div>
    </form>
  );
}

function ReviewRow({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-sm text-foreground-muted">{label}</dt>
      <dd className="min-w-0 break-words text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}
