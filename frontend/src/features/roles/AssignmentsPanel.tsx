import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Badge, Button, ConfirmDialog, FormError, Label, Select, Switch } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import { useCreateAssignment, useRevokeAssignment, useRolesQuery } from "./queries";
import { isManageableRole } from "./roleAccess";
import { type Assignment, type UserWithAssignments } from "./types";

const schema = z.object({
  role_id: z.string().min(1, "Выберите роль"),
  branch_id: z.string(),
  password_required: z.boolean(),
});

type FormValues = z.infer<typeof schema>;
const EMPTY_ASSIGNMENT_FORM: FormValues = {
  role_id: "",
  branch_id: "",
  password_required: false,
};

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
    defaultValues: EMPTY_ASSIGNMENT_FORM,
  });

  const closeAddForm = () => {
    form.reset(EMPTY_ASSIGNMENT_FORM);
    setTopError(null);
    setAddOpen(false);
  };

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
    const normalizedBranchId = d.branch_id === "" ? null : d.branch_id;
    const duplicateAssignment = user.assignments.some(
      (assignment) =>
        assignment.is_active &&
        assignment.role_id === d.role_id &&
        assignment.branch_id === normalizedBranchId,
    );
    if (duplicateAssignment) {
      setTopError("Эта роль уже назначена сотруднику для выбранной области");
      return;
    }
    try {
      await createAssignment.mutateAsync({
        userId: user.id,
        payload: {
          role_id: d.role_id,
          branch_id: normalizedBranchId,
          password_required: d.password_required,
        },
      });
      closeAddForm();
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
  const assignmentRoleName = (assignment: Assignment) =>
    assignment.role_name ?? roleById(assignment.role_id)?.name ?? "Недоступная роль";
  const canRevokeRole = (roleId: string): boolean => {
    const role = roleById(roleId);
    return Boolean(role && isManageableRole(role, tenantId));
  };
  const branchName = (branchId: string | null) =>
    branchId
      ? (branches.data?.find((branch) => branch.id === branchId)?.name ?? "Недоступная точка")
      : "Все точки аптеки";
  const activeAssignments = user.assignments.filter((assignment) => assignment.is_active);
  const revokedAssignments = user.assignments.filter((assignment) => !assignment.is_active);
  const pendingAssignment =
    user.assignments.find((assignment) => assignment.id === pendingRevokeId) ?? null;

  const assignmentItem = (assignment: Assignment, showRevoke: boolean) => (
    <li
      key={assignment.id}
      className="flex flex-col gap-3 rounded-lg border border-border bg-background px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{assignmentRoleName(assignment)}</span>
          <Badge tone={assignment.is_active ? "success" : "neutral"}>
            {assignment.is_active ? "активна" : "отозвана"}
          </Badge>
          {assignment.password_required && <Badge tone="info">пароль при входе</Badge>}
        </div>
        <p className="mt-1 flex items-center gap-1.5 text-xs text-foreground-muted">
          <ScopeIcon />
          {branchName(assignment.branch_id)}
        </p>
      </div>
      {showRevoke && canManage && canRevokeRole(assignment.role_id) && (
        <Button
          variant="ghost"
          size="sm"
          className="self-end sm:self-auto"
          onClick={() => {
            setRevokeError(null);
            setPendingRevokeId(assignment.id);
          }}
          isLoading={revokeAssignment.isPending && pendingRevokeId === assignment.id}
        >
          Отозвать
        </Button>
      )}
    </li>
  );

  return (
    <div className="space-y-4">
      <div className="flex min-w-0 items-center justify-between gap-3 border-b border-border pb-4">
        <div className="min-w-0">
          <p className="break-words font-semibold text-foreground">{user.full_name}</p>
          <p className="break-all text-sm text-foreground-muted">{user.email}</p>
        </div>
        <Badge tone={activeAssignments.length > 0 ? "success" : "warning"}>
          Активных: {activeAssignments.length}
        </Badge>
      </div>

      <section aria-labelledby="active-assignments-heading">
        <div className="flex items-center justify-between gap-3">
          <h3 id="active-assignments-heading" className="text-sm font-semibold text-foreground">
            Действующий доступ
          </h3>
        </div>
        {activeAssignments.length === 0 ? (
          <p className="mt-2 rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-foreground-muted">
            Активных ролей пока нет
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {activeAssignments.map((assignment) => assignmentItem(assignment, true))}
          </ul>
        )}
        {revokedAssignments.length > 0 && (
          <details className="mt-3 border-t border-border pt-3">
            <summary className="cursor-pointer text-sm font-medium text-foreground-muted hover:text-foreground">
              История отозванных ролей · {revokedAssignments.length}
            </summary>
            <ul className="mt-2 space-y-2">
              {revokedAssignments.map((assignment) => assignmentItem(assignment, false))}
            </ul>
          </details>
        )}
      </section>

      {roles.error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {describeApiError(roles.error, "Не удалось загрузить доступные роли")}
        </p>
      )}
      {branches.error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
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
          className="space-y-3 rounded-lg border border-border bg-background p-3"
        >
          <h3 className="text-sm font-semibold text-foreground">Новое назначение</h3>
          <div>
            <Label htmlFor="role_id">Роль</Label>
            <Select
              id="role_id"
              invalid={Boolean(form.formState.errors.role_id)}
              aria-describedby={form.formState.errors.role_id ? "role-id-error" : undefined}
              {...form.register("role_id")}
            >
              <option value="">— выберите —</option>
              {manageableRoles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
            <FormError id="role-id-error">{form.formState.errors.role_id?.message}</FormError>
          </div>
          <div>
            <Label htmlFor="branch_id">Точка</Label>
            <Select id="branch_id" {...form.register("branch_id")}>
              <option value="">Все точки аптеки</option>
              {branches.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </Select>
          </div>
          <Switch label="Требовать пароль при входе" {...form.register("password_required")} />
          {topError && (
            <p
              className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
              role="alert"
            >
              {topError}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-3">
            <Button type="button" variant="ghost" size="sm" onClick={closeAddForm}>
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
          <PlusIcon />
          Назначить роль
        </Button>
      ) : canManage &&
        !roles.isLoading &&
        !branches.isLoading &&
        !roles.error &&
        !branches.error ? (
        <p className="text-sm text-foreground-muted">Нет доступных для назначения ролей.</p>
      ) : null}

      <div className="flex justify-end border-t border-border pt-3">
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>

      <ConfirmDialog
        open={pendingRevokeId !== null}
        title="Отозвать роль"
        message={
          <>
            {pendingAssignment ? (
              <>
                Отозвать роль «{assignmentRoleName(pendingAssignment)}» у сотрудника «
                {user.full_name}» для области «{branchName(pendingAssignment.branch_id)}»? Доступ
                прекратится сразу.
              </>
            ) : (
              "Роль перестанет действовать для этого сотрудника."
            )}
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

function PlusIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function ScopeIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className="shrink-0"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" />
      <circle cx="12" cy="10" r="2.5" />
    </svg>
  );
}
