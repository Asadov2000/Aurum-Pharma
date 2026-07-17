import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, ConfirmDialog, FormError, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import { useCreateAssignment, useRevokeAssignment, useRolesQuery } from "./queries";
import { isManageableRole } from "./roleAccess";
import { type UserWithAssignments } from "./types";

const schema = z.object({
  role_id: z.string().min(1, "Выберите роль"),
  branch_id: z.string(),
  password_required: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function AssignmentsPanel({
  user,
  tenantId,
  canManage,
  onClose,
}: {
  user: UserWithAssignments;
  tenantId: string | null;
  canManage: boolean;
  onClose: () => void;
}): JSX.Element {
  const roles = useRolesQuery(canManage);
  const branches = useBranchesQuery(true, canManage);
  const createAssignment = useCreateAssignment();
  const revokeAssignment = useRevokeAssignment();
  const [topError, setTopError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRevokeId, setPendingRevokeId] = useState<string | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: { role_id: "", branch_id: "", password_required: false },
  });

  const onAdd = form.handleSubmit(async (values) => {
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
    const selectedRole = roles.data?.find((role) => role.id === d.role_id);
    if (
      !canManage ||
      !selectedRole ||
      !selectedRole.is_active ||
      !isManageableRole(selectedRole, tenantId)
    ) {
      setTopError("Эта роль недоступна для назначения");
      return;
    }
    if (d.branch_id && !branches.data?.some((branch) => branch.id === d.branch_id)) {
      setTopError("Эта точка недоступна для назначения");
      return;
    }
    try {
      await createAssignment.mutateAsync({
        userId: user.id,
        payload: {
          role_id: d.role_id,
          branch_id: d.branch_id === "" ? null : d.branch_id,
          password_required: d.password_required,
        },
      });
      form.reset({ role_id: "", branch_id: "", password_required: false });
      setAddOpen(false);
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось назначить роль"));
    }
  });

  const confirmRevoke = async () => {
    if (!pendingRevokeId) return;
    setRevokeError(null);
    try {
      await revokeAssignment.mutateAsync({ userId: user.id, assignmentId: pendingRevokeId });
      setPendingRevokeId(null);
    } catch (err) {
      setRevokeError(describeApiError(err, "Не удалось отозвать"));
    }
  };

  const manageableRoles = (roles.data ?? []).filter(
    (role) => role.is_active && isManageableRole(role, tenantId),
  );
  const roleById = (roleId: string) => roles.data?.find((role) => role.id === roleId);
  const roleName = (roleId: string) => roleById(roleId)?.name ?? roleId.slice(0, 8);
  const canRevokeRole = (roleId: string): boolean => {
    const role = roleById(roleId);
    return Boolean(role && isManageableRole(role, tenantId));
  };
  const branchName = (branchId: string | null) =>
    branchId
      ? (branches.data?.find((branch) => branch.id === branchId)?.name ?? branchId.slice(0, 8))
      : "все точки";

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm text-foreground-muted">Назначения роли</p>
        {user.assignments.length === 0 ? (
          <p className="text-sm italic text-foreground-muted">Ролей пока нет</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {user.assignments.map((a) => (
              <li
                key={a.id}
                className="flex items-center justify-between rounded-md border border-border bg-surface px-3 py-2"
              >
                <div className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{roleName(a.role_id)}</span>
                    <Badge tone={a.is_active ? "success" : "neutral"}>
                      {a.is_active ? "активна" : "отозвана"}
                    </Badge>
                    {a.password_required && <Badge tone="info">пароль</Badge>}
                  </div>
                  <p className="text-xs text-foreground-muted">{branchName(a.branch_id)}</p>
                </div>
                {a.is_active && canManage && canRevokeRole(a.role_id) && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setRevokeError(null);
                      setPendingRevokeId(a.id);
                    }}
                    isLoading={revokeAssignment.isPending}
                  >
                    Отозвать
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {roles.error && (
        <p className="text-sm text-danger">
          {describeApiError(roles.error, "Не удалось загрузить доступные роли")}
        </p>
      )}
      {branches.error && (
        <p className="text-sm text-danger">
          {describeApiError(branches.error, "Не удалось загрузить точки")}
        </p>
      )}
      {canManage && (roles.isLoading || branches.isLoading) && (
        <p className="text-sm text-foreground-muted">Загрузка доступных ролей…</p>
      )}

      {canManage && addOpen ? (
        <form
          onSubmit={onAdd}
          noValidate
          className="space-y-3 rounded-md border border-border bg-foreground/[0.03] p-3"
        >
          <div>
            <Label htmlFor="role_id">Роль</Label>
            <Select
              id="role_id"
              invalid={Boolean(form.formState.errors.role_id)}
              {...form.register("role_id")}
            >
              <option value="">— выберите —</option>
              {manageableRoles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
            <FormError>{form.formState.errors.role_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="branch_id">Точка</Label>
            <Select id="branch_id" {...form.register("branch_id")}>
              <option value="">— любая —</option>
              {branches.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
          </div>
          <Switch label="Требовать пароль" {...form.register("password_required")} />
          {topError && <p className="text-sm text-danger">{topError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setAddOpen(false)}>
              Отмена
            </Button>
            <Button
              type="submit"
              size="sm"
              isLoading={form.formState.isSubmitting}
              disabled={roles.isError || branches.isError}
            >
              Добавить
            </Button>
          </div>
        </form>
      ) : canManage &&
        !roles.isLoading &&
        !branches.isLoading &&
        !roles.error &&
        !branches.error &&
        manageableRoles.length > 0 ? (
        <Button variant="secondary" onClick={() => setAddOpen(true)}>
          + Назначить роль
        </Button>
      ) : canManage &&
        !roles.isLoading &&
        !branches.isLoading &&
        !roles.error &&
        !branches.error ? (
        <p className="text-sm text-foreground-muted">Нет доступных для назначения ролей.</p>
      ) : null}

      <div className="flex justify-end">
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>

      <ConfirmDialog
        open={pendingRevokeId !== null}
        title="Отозвать роль"
        message={
          <>
            Роль перестанет действовать для этого пользователя.
            {revokeError && <span className="mt-2 block text-danger">{revokeError}</span>}
          </>
        }
        confirmLabel="Отозвать"
        variant="danger"
        isLoading={revokeAssignment.isPending}
        onConfirm={() => void confirmRevoke()}
        onCancel={() => {
          setPendingRevokeId(null);
          setRevokeError(null);
        }}
      />
    </div>
  );
}
