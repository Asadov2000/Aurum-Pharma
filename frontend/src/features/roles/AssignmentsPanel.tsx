import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  FormError,
  Label,
  SegmentedControl,
  Select,
  Switch,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";
import { useBranchesQuery } from "@/features/foundation/queries";

import {
  useAssignmentHistoryQuery,
  usePermissionsQuery,
  useReplaceAssignments,
  useRevokeAssignment,
  useRolesQuery,
} from "./queries";
import { GROUP_LABEL } from "./labels";
import { hasUnavailableRolePermissions, isManageableRole } from "./roleAccess";
import { type Assignment, type Permission, type Role, type UserWithAssignments } from "./types";

const schema = z
  .object({
    role_id: z.string().min(1, "Выберите роль"),
    scope_mode: z.enum(["tenant", "branches"]),
    branch_ids: z.array(z.string()),
    password_required: z.boolean(),
    replace_all: z.boolean(),
  })
  .superRefine((values, context) => {
    if (values.scope_mode === "branches" && values.branch_ids.length === 0) {
      context.addIssue({
        code: "custom",
        path: ["branch_ids"],
        message: "Выберите хотя бы одну торговую точку",
      });
    }
  });

type FormValues = z.infer<typeof schema>;
type ResolvedAssignment = {
  role: Role;
  branchIds: Array<string | null>;
  replacements: Assignment[];
  removals: Assignment[];
};
const EMPTY_ASSIGNMENT_FORM: FormValues = {
  role_id: "",
  scope_mode: "tenant",
  branch_ids: [],
  password_required: false,
  replace_all: false,
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
  const permissions = usePermissionsQuery(canManage);
  const history = useAssignmentHistoryQuery(user.id, canManage);
  const replaceAssignments = useReplaceAssignments();
  const revokeAssignment = useRevokeAssignment();
  const [topError, setTopError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [pendingRevokeId, setPendingRevokeId] = useState<string | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);
  const [pendingAdd, setPendingAdd] = useState<FormValues | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const form = useForm<FormValues>({
    defaultValues: EMPTY_ASSIGNMENT_FORM,
  });
  const scopeMode = form.watch("scope_mode");

  const closeAddForm = () => {
    form.reset(EMPTY_ASSIGNMENT_FORM);
    setTopError(null);
    setPendingAdd(null);
    setAddOpen(false);
  };

  const resolveAssignment = (values: FormValues): ResolvedAssignment | string => {
    const selectedRole = roles.data?.find((role) => role.id === values.role_id);
    if (
      !canManage ||
      !selectedRole ||
      !selectedRole.is_active ||
      hasUnavailableRolePermissions(selectedRole) ||
      !isManageableRole(selectedRole, tenantId)
    ) {
      return "Эта роль недоступна для назначения";
    }
    const branchIds: Array<string | null> =
      values.scope_mode === "tenant" ? [null] : values.branch_ids;
    if (branchIds.length === 0) return "Выберите хотя бы одну торговую точку";
    if (
      branchIds.some(
        (branchId) =>
          branchId !== null &&
          !branches.data?.some((branch) => branch.id === branchId && branch.is_active),
      )
    ) {
      return "Одна из торговых точек недоступна для назначения";
    }
    const activeByScope = branchIds.flatMap((branchId) => {
      const assignment = user.assignments.find(
        (item) => item.is_active && item.branch_id === branchId,
      );
      return assignment ? [assignment] : [];
    });
    const unchanged = activeByScope.filter(
      (assignment) =>
        assignment.role_id === values.role_id &&
        assignment.password_required === values.password_required,
    );
    const removals = values.replace_all
      ? user.assignments.filter(
          (assignment) => assignment.is_active && !branchIds.includes(assignment.branch_id),
        )
      : [];
    if (unchanged.length === branchIds.length && removals.length === 0) {
      return "Эта роль с такими настройками уже действует во всех выбранных областях";
    }
    return {
      role: selectedRole,
      branchIds,
      replacements: activeByScope.filter(
        (assignment) =>
          assignment.role_id !== values.role_id ||
          assignment.password_required !== values.password_required,
      ),
      removals,
    };
  };

  const onReview = form.handleSubmit((values) => {
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
    setSuccessMessage(null);
    const resolved = resolveAssignment(parsed.data);
    if (typeof resolved === "string") {
      setTopError(resolved);
      return;
    }
    setPendingAdd(parsed.data);
  });

  const confirmAdd = async () => {
    if (!pendingAdd) return;
    setTopError(null);
    const resolved = resolveAssignment(pendingAdd);
    if (typeof resolved === "string") {
      setPendingAdd(null);
      setTopError(resolved);
      return;
    }
    try {
      await replaceAssignments.mutateAsync({
        userId: user.id,
        payload: {
          role_id: resolved.role.id,
          branch_ids: resolved.branchIds,
          password_required: pendingAdd.password_required,
          replace_all: pendingAdd.replace_all,
        },
      });
      closeAddForm();
      setSuccessMessage(
        `Роль «${resolved.role.name}» применена: ${resolved.branchIds.length} ${scopeWord(resolved.branchIds.length)}. Доступ уже действует.`,
      );
    } catch (err) {
      setPendingAdd(null);
      setTopError(describeApiError(err, "Не удалось назначить роль"));
    }
  };

  const confirmRevoke = async () => {
    if (!pendingRevokeId) return;
    setRevokeError(null);
    setSuccessMessage(null);
    const assignment = user.assignments.find((item) => item.id === pendingRevokeId) ?? null;
    try {
      await revokeAssignment.mutateAsync({ userId: user.id, assignmentId: pendingRevokeId });
      setPendingRevokeId(null);
      setSuccessMessage(
        assignment
          ? `Роль «${assignmentRoleName(assignment)}» отозвана. Сотрудник больше не может использовать этот доступ.`
          : "Роль отозвана. Сотрудник больше не может использовать этот доступ.",
      );
    } catch (err) {
      setRevokeError(describeApiError(err, "Не удалось отозвать"));
    }
  };

  const manageableRoles = (roles.data ?? []).filter(
    (role) =>
      role.is_active && !hasUnavailableRolePermissions(role) && isManageableRole(role, tenantId),
  );
  const roleById = (roleId: string) => roles.data?.find((role) => role.id === roleId);
  const assignmentRoleName = (assignment: Assignment) =>
    assignment.role_name ?? roleById(assignment.role_id)?.name ?? "Недоступная роль";
  const canRevokeRole = (roleId: string): boolean => {
    const role = roleById(roleId);
    return Boolean(role && isManageableRole(role, tenantId));
  };
  const branchById = (branchId: string | null) =>
    branchId ? branches.data?.find((branch) => branch.id === branchId) : undefined;
  const branchName = (branchId: string | null) => {
    if (!branchId) return "Все точки аптеки";
    const branch = branchById(branchId);
    if (!branch) return "Недоступная точка";
    return branch.is_active ? branch.name : `${branch.name} · точка отключена`;
  };
  const assignmentIsPaused = (assignment: Assignment) =>
    assignment.is_active &&
    assignment.branch_id !== null &&
    branchById(assignment.branch_id)?.is_active === false;
  const activeAssignments = user.assignments.filter((assignment) => assignment.is_active);
  const revokedAssignments = user.assignments.filter((assignment) => !assignment.is_active);
  const pausedAssignmentCount = activeAssignments.filter(assignmentIsPaused).length;
  const effectiveAssignmentCount = activeAssignments.length - pausedAssignmentCount;
  const pendingAssignment =
    user.assignments.find((assignment) => assignment.id === pendingRevokeId) ?? null;
  const pendingResolved = pendingAdd ? resolveAssignment(pendingAdd) : null;
  const pendingRole =
    pendingResolved && typeof pendingResolved !== "string" ? pendingResolved.role : null;
  const pendingBranchIds =
    pendingResolved && typeof pendingResolved !== "string" ? pendingResolved.branchIds : [];
  const pendingReplacements =
    pendingResolved && typeof pendingResolved !== "string" ? pendingResolved.replacements : [];
  const pendingRemovals =
    pendingResolved && typeof pendingResolved !== "string" ? pendingResolved.removals : [];
  const visiblePermissionByCode = new Map(
    (permissions.data ?? [])
      .filter(isVisibleTenantPermission)
      .map((permission) => [permission.code, permission]),
  );
  const pendingPermissions = (pendingRole?.permissions ?? []).flatMap((code) => {
    const permission = visiblePermissionByCode.get(code);
    return permission ? [permission] : [];
  });
  const pendingTenantWideOmissions = pendingBranchIds.includes(null)
    ? []
    : pendingPermissions.filter((permission) => permission.scope_type === "TENANT_ALL");
  const pendingEffectivePermissions = pendingPermissions.filter(
    (permission) => !pendingTenantWideOmissions.includes(permission),
  );
  const pendingRiskPermissions = pendingEffectivePermissions.filter(isRiskPermission);
  const pendingCapabilityGroups = [
    ...new Set(
      pendingEffectivePermissions.map(
        (permission) => GROUP_LABEL[permission.group_code] ?? "Другие функции",
      ),
    ),
  ];

  const assignmentItem = (assignment: Assignment, showRevoke: boolean) => (
    <li
      key={assignment.id}
      className="flex flex-col gap-3 rounded-lg border border-border bg-background px-3 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{assignmentRoleName(assignment)}</span>
          <Badge
            tone={
              assignmentIsPaused(assignment)
                ? "warning"
                : assignment.is_active
                  ? "success"
                  : "neutral"
            }
          >
            {assignmentIsPaused(assignment)
              ? "приостановлена"
              : assignment.is_active
                ? "активна"
                : "отозвана"}
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
            setSuccessMessage(null);
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
        <Badge tone={effectiveAssignmentCount > 0 ? "success" : "warning"}>
          Действует: {effectiveAssignmentCount}
          {pausedAssignmentCount > 0 ? ` · приостановлено: ${pausedAssignmentCount}` : ""}
        </Badge>
      </div>

      {successMessage ? (
        <p
          className="rounded-lg border border-success/30 bg-success-subtle px-3 py-2 text-sm text-success-foreground"
          role="status"
        >
          {successMessage}
        </p>
      ) : null}

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

      {canManage ? (
        <section aria-labelledby="access-history-heading" className="border-t border-border pt-3">
          <details>
            <summary
              id="access-history-heading"
              className="cursor-pointer text-sm font-semibold text-foreground hover:text-primary"
            >
              Журнал изменений доступа
              {history.data ? ` · ${history.data.length}` : ""}
            </summary>
            {history.isLoading ? (
              <p className="mt-3 text-sm text-foreground-muted">Загружаем историю…</p>
            ) : history.error ? (
              <p
                className="mt-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
                role="alert"
              >
                {describeApiError(history.error, "Не удалось загрузить историю доступа")}
              </p>
            ) : history.data && history.data.length > 0 ? (
              <ol className="mt-3 space-y-2">
                {history.data.map((event) => (
                  <li
                    key={event.id}
                    className="rounded-lg border border-border bg-background px-3 py-2.5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={historyEventTone(event.event_type)}>
                          {historyEventLabel(event.event_type)}
                        </Badge>
                        <span className="font-medium text-foreground">{event.role_name}</span>
                      </div>
                      <time className="text-xs text-foreground-muted" dateTime={event.created_at}>
                        {formatHistoryDate(event.created_at)}
                      </time>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-foreground-muted">
                      {event.branch_id ? event.branch_name || "Недоступная точка" : "Вся аптека"}
                      {` · ${event.actor_name}`}
                    </p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-3 text-sm text-foreground-muted">Изменений доступа пока нет.</p>
            )}
          </details>
        </section>
      ) : null}

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
          {describeApiError(branches.error, "Не удалось загрузить торговые точки")}
        </p>
      )}
      {permissions.error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {describeApiError(permissions.error, "Не удалось загрузить доступные возможности")}
        </p>
      )}
      {canManage && (roles.isLoading || branches.isLoading || permissions.isLoading) && (
        <p className="text-sm text-foreground-muted">Подготавливаем доступные роли…</p>
      )}

      {canManage && addOpen ? (
        <form
          onSubmit={onReview}
          noValidate
          className="space-y-3 rounded-lg border border-border bg-background p-3"
        >
          <div>
            <h3 className="text-sm font-semibold text-foreground">Назначение роли</h3>
            <p className="mt-1 text-xs leading-5 text-foreground-muted">
              Выберите, что сотрудник сможет делать и где будет действовать его доступ.
            </p>
          </div>
          <div>
            <Label htmlFor="role_id">Роль</Label>
            <Select
              id="role_id"
              invalid={Boolean(form.formState.errors.role_id)}
              aria-describedby={form.formState.errors.role_id ? "role-id-error" : undefined}
              {...form.register("role_id")}
            >
              <option value="">Выберите роль</option>
              {manageableRoles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </Select>
            <FormError id="role-id-error">{form.formState.errors.role_id?.message}</FormError>
          </div>
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-foreground">Где действует роль</legend>
            <SegmentedControl
              label="Область действия роли"
              value={scopeMode}
              options={[
                { value: "tenant", label: "Во всей аптеке" },
                { value: "branches", label: "В выбранных точках" },
              ]}
              onChange={(value) => {
                form.setValue("scope_mode", value, { shouldValidate: true });
                form.setValue("branch_ids", [], { shouldValidate: true });
              }}
              className="w-full sm:w-auto"
            />
            {scopeMode === "tenant" ? (
              <p className="rounded-lg border border-info/30 bg-info-subtle px-3 py-2 text-sm text-info-foreground">
                Роль будет действовать во всех текущих и новых торговых точках этой аптеки.
              </p>
            ) : branches.data && branches.data.some((branch) => branch.is_active) ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {branches.data
                  .filter((branch) => branch.is_active)
                  .map((branch) => (
                    <label
                      key={branch.id}
                      className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2 text-sm hover:border-primary/50"
                    >
                      <Checkbox value={branch.id} {...form.register("branch_ids")} />
                      <span className="min-w-0 break-words font-medium text-foreground">
                        {branch.name}
                      </span>
                    </label>
                  ))}
              </div>
            ) : (
              <p className="rounded-lg border border-warning/30 bg-warning-subtle px-3 py-2 text-sm text-warning-foreground">
                Активных торговых точек пока нет.
              </p>
            )}
            <FormError id="branch-ids-error">{form.formState.errors.branch_ids?.message}</FormError>
          </fieldset>
          {user.can_require_password ? (
            <Switch
              label="Запрашивать пароль при входе с этой ролью"
              {...form.register("password_required")}
            />
          ) : (
            <p className="rounded-lg border border-border bg-surface-subtle px-3 py-2 text-sm text-foreground-muted">
              Обязательный пароль станет доступен после настройки пароля сотрудником.
            </p>
          )}
          {activeAssignments.length > 0 ? (
            <div className="rounded-lg border border-border bg-surface-subtle p-3">
              <Switch label="Оставить только выбранный доступ" {...form.register("replace_all")} />
              <p className="mt-1 pl-12 text-xs leading-5 text-foreground-muted">
                Используйте при переводе сотрудника. Все другие активные назначения будут отозваны
                одной операцией; при ошибке прежний доступ сохранится.
              </p>
            </div>
          ) : null}
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
              disabled={
                roles.isError ||
                branches.isError ||
                permissions.isError ||
                roles.isLoading ||
                branches.isLoading ||
                permissions.isLoading
              }
            >
              Проверить доступ
            </Button>
          </div>
        </form>
      ) : canManage &&
        !roles.isLoading &&
        !branches.isLoading &&
        !permissions.isLoading &&
        !roles.error &&
        !branches.error &&
        !permissions.error &&
        manageableRoles.length > 0 ? (
        <Button
          variant="secondary"
          onClick={() => {
            setSuccessMessage(null);
            setAddOpen(true);
          }}
        >
          <PlusIcon />
          Назначить роль
        </Button>
      ) : canManage &&
        !roles.isLoading &&
        !branches.isLoading &&
        !permissions.isLoading &&
        !roles.error &&
        !branches.error &&
        !permissions.error ? (
        <p className="text-sm text-foreground-muted">Нет доступных для назначения ролей.</p>
      ) : null}

      <div className="flex justify-end border-t border-border pt-3">
        <Button variant="ghost" onClick={onClose}>
          Закрыть
        </Button>
      </div>

      <ConfirmDialog
        open={pendingAdd !== null && pendingRole !== null}
        title="Проверьте доступ сотрудника"
        message={
          pendingRole ? (
            <div className="space-y-4">
              <dl className="grid gap-3 rounded-lg border border-border bg-background p-3 sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-foreground-muted">Роль</dt>
                  <dd className="mt-1 font-semibold text-foreground">{pendingRole.name}</dd>
                </div>
                <div>
                  <dt className="text-xs text-foreground-muted">Где действует</dt>
                  <dd className="mt-1 font-semibold text-foreground">
                    {pendingBranchIds.map(branchName).join(", ")}
                  </dd>
                </div>
              </dl>

              {pendingReplacements.length > 0 ? (
                <div className="rounded-lg border border-info/40 bg-info-subtle p-3 text-info-foreground">
                  <p className="font-semibold">Роль будет заменена без разрыва доступа</p>
                  <ul className="mt-2 space-y-1">
                    {pendingReplacements.map((assignment) => (
                      <li key={assignment.id}>
                        {branchName(assignment.branch_id)}: «{assignmentRoleName(assignment)}» → «
                        {pendingRole.name}»
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-sm">
                    Все изменения применятся одной операцией. При ошибке старый доступ сохранится.
                  </p>
                </div>
              ) : null}

              {pendingRemovals.length > 0 ? (
                <div className="rounded-lg border border-warning/40 bg-warning-subtle p-3 text-warning-foreground">
                  <p className="font-semibold">Другой доступ будет отозван</p>
                  <ul className="mt-2 space-y-1">
                    {pendingRemovals.map((assignment) => (
                      <li key={assignment.id}>
                        {branchName(assignment.branch_id)}: «{assignmentRoleName(assignment)}»
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-sm">
                    Это перевод сотрудника: после подтверждения останутся только выбранные выше
                    назначения.
                  </p>
                </div>
              ) : null}

              <div>
                <p className="font-medium text-foreground">Кратко о возможностях</p>
                {pendingEffectivePermissions.length > 0 ? (
                  <>
                    <p className="mt-1 leading-5">Разделы: {pendingCapabilityGroups.join(", ")}.</p>
                    <ul className="mt-2 max-h-56 list-disc space-y-1 overflow-y-auto pl-5 pr-2 text-foreground-secondary">
                      {pendingEffectivePermissions.map((permission) => (
                        <li key={permission.code}>{permission.name}</li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="mt-1 leading-5 text-foreground-muted">
                    Для этой роли нет доступных действий.
                  </p>
                )}
              </div>

              {pendingTenantWideOmissions.length > 0 ? (
                <div className="rounded-lg border border-warning/40 bg-warning-subtle p-3 text-warning-foreground">
                  <p className="font-semibold">Не включатся для отдельных точек</p>
                  <p className="mt-1 text-sm leading-5">
                    Эти возможности работают только при выборе «Во всей аптеке»:
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {pendingTenantWideOmissions.map((permission) => (
                      <li key={permission.code}>{permission.name}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {pendingRiskPermissions.length > 0 ? (
                <div className="rounded-lg border border-warning/40 bg-warning-subtle p-3 text-warning-foreground">
                  <p className="font-semibold">Обратите внимание</p>
                  <p className="mt-1 leading-5">
                    Роль включает важные действия. Назначайте её только сотруднику, которому вы
                    доверяете:
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {pendingRiskPermissions.map((permission) => (
                      <li key={permission.code}>{permission.name}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null
        }
        confirmLabel={
          pendingRemovals.length > 0
            ? "Перевести и применить"
            : pendingReplacements.length > 0
              ? "Заменить и применить"
              : "Применить доступ"
        }
        isLoading={replaceAssignments.isPending}
        onConfirm={() => void confirmAdd()}
        onCancel={() => setPendingAdd(null)}
      />

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

function isVisibleTenantPermission(permission: Permission): boolean {
  return (
    permission.is_active &&
    permission.target_role_type === "tenant" &&
    permission.scope_type !== "PLATFORM"
  );
}

function isRiskPermission(permission: Permission): boolean {
  return (
    permission.is_dangerous ||
    permission.risk_level !== "normal" ||
    permission.requires_confirmation ||
    permission.requires_step_up
  );
}

function historyEventLabel(event: "assigned" | "changed" | "restored" | "revoked"): string {
  if (event === "assigned") return "Назначено";
  if (event === "restored") return "Восстановлено";
  if (event === "revoked") return "Отозвано";
  return "Изменено";
}

function historyEventTone(
  event: "assigned" | "changed" | "restored" | "revoked",
): "success" | "info" | "warning" | "neutral" {
  if (event === "assigned" || event === "restored") return "success";
  if (event === "revoked") return "warning";
  return "info";
}

function formatHistoryDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Dushanbe",
  }).format(new Date(value));
}

function scopeWord(count: number): string {
  if (count % 10 === 1 && count % 100 !== 11) return "область";
  if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) {
    return "области";
  }
  return "областей";
}
