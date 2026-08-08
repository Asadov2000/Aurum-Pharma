import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  FormError,
  Input,
  Label,
  Select,
  Textarea,
} from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { groupLabel } from "./labels";
import { useCreateRole, usePermissionsQuery, useTemplatesQuery, useUpdateRole } from "./queries";
import { hasUnavailableRolePermissions, ROLE_EDIT_BLOCKED_MESSAGE } from "./roleAccess";
import { type Permission, type Role } from "./types";

interface Props {
  mode: "create" | "edit";
  role?: Role;
  onClose: () => void;
  onCancel?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}

const roleFormSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Введите название роли")
    .max(100, "Название должно быть не длиннее 100 символов"),
  description: z.string().max(500, "Описание должно быть не длиннее 500 символов"),
});

type RoleFormValues = z.infer<typeof roleFormSchema>;

interface RoleSubmission {
  name: string;
  description: string | null;
  permissions: string[];
}

interface PendingDangerousSubmission {
  values: RoleSubmission;
  permissions: Permission[];
}

/** Group permissions by group_code, preserving the API's order (it returns them
 *  sorted by group_code, code), keeping only the ones passed in. */
function groupBy(perms: Permission[]): { code: string; items: Permission[] }[] {
  const order: string[] = [];
  const byGroup = new Map<string, Permission[]>();
  for (const p of perms) {
    if (!byGroup.has(p.group_code)) {
      byGroup.set(p.group_code, []);
      order.push(p.group_code);
    }
    byGroup.get(p.group_code)!.push(p);
  }
  return order.map((code) => ({ code, items: byGroup.get(code) ?? [] }));
}

export function RoleBuilderModal({
  mode,
  role,
  onClose,
  onCancel = onClose,
  onDirtyChange,
}: Props): JSX.Element {
  const permsQuery = usePermissionsQuery();
  const templatesQuery = useTemplatesQuery(mode === "create");
  const createRole = useCreateRole();
  const updateRole = useUpdateRole();

  const form = useForm<RoleFormValues>({
    defaultValues: {
      name: role?.name ?? "",
      description: role?.description ?? "",
    },
  });
  const [templateId, setTemplateId] = useState("");
  const [checked, setChecked] = useState<Set<string>>(() => new Set(role?.permissions ?? []));
  const [permissionSearch, setPermissionSearch] = useState("");
  const [activeGroup, setActiveGroup] = useState("all");
  const [topError, setTopError] = useState<string | null>(null);
  const [pendingDangerousSubmission, setPendingDangerousSubmission] =
    useState<PendingDangerousSubmission | null>(null);

  // The server returns the grantable catalogue for this actor. The client must
  // neither expand it for support accounts nor infer delegation from JWT data.
  const visible = useMemo(() => permsQuery.data ?? [], [permsQuery.data]);
  const visibleCodes = useMemo(() => new Set(visible.map((p) => p.code)), [visible]);
  const catalogGroups = useMemo(() => groupBy(visible), [visible]);
  const normalizedSearch = permissionSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredVisible = useMemo(() => {
    return visible.filter((permission) => {
      if (activeGroup !== "all" && permission.group_code !== activeGroup) {
        return false;
      }
      if (!normalizedSearch) return true;
      const haystack = [
        permission.name,
        permission.description ?? "",
        permission.code,
        groupLabel(permission.group_code),
      ]
        .join(" ")
        .toLocaleLowerCase("ru-RU");
      return haystack.includes(normalizedSearch);
    });
  }, [activeGroup, normalizedSearch, visible]);
  const groups = useMemo(() => groupBy(filteredVisible), [filteredVisible]);
  const editBlocked =
    mode === "edit" &&
    role !== undefined &&
    permsQuery.isSuccess &&
    hasUnavailableRolePermissions(role);

  const toggle = (code: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });

  const toggleGroup = (items: Permission[]) => {
    setChecked((previous) => {
      const next = new Set(previous);
      const allSelected = items.every((permission) => next.has(permission.code));
      for (const permission of items) {
        if (allSelected) next.delete(permission.code);
        else next.add(permission.code);
      }
      return next;
    });
  };

  const onTemplateChange = (id: string) => {
    setTemplateId(id);
    const tpl = templatesQuery.data?.find((t) => t.id === id);
    if (!tpl) {
      setChecked(new Set());
      return;
    }
    // Templates are hints only and may tick exclusively grantable catalogue items.
    setChecked(new Set(tpl.permissions.filter((c) => visibleCodes.has(c))));
  };

  const saveRole = async (values: RoleSubmission): Promise<void> => {
    setTopError(null);
    try {
      if (mode === "edit" && role) {
        await updateRole.mutateAsync({
          id: role.id,
          payload: {
            expected_version: role.version,
            ...values,
          },
        });
      } else {
        await createRole.mutateAsync(values);
      }
      setPendingDangerousSubmission(null);
      onClose();
    } catch (err) {
      setPendingDangerousSubmission(null);
      setTopError(describeApiError(err, "Не удалось сохранить роль"));
    }
  };

  const onSubmit = form.handleSubmit(async (values) => {
    form.clearErrors();
    if (editBlocked) {
      setTopError(ROLE_EDIT_BLOCKED_MESSAGE);
      return;
    }
    const parsed = roleFormSchema.safeParse(values);
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "name" || field === "description") {
          form.setError(field, { message: issue.message });
        }
      }
      return;
    }
    const codes = [...checked].filter((code) => visibleCodes.has(code));
    const submission: RoleSubmission = {
      name: parsed.data.name,
      description: parsed.data.description.trim() || null,
      permissions: codes,
    };
    const originalCodes = new Set(role?.permissions ?? []);
    const newlyAddedDangerousPermissions = visible.filter(
      (permission) =>
        permission.is_dangerous &&
        codes.includes(permission.code) &&
        !originalCodes.has(permission.code),
    );
    if (newlyAddedDangerousPermissions.length > 0) {
      setPendingDangerousSubmission({
        values: submission,
        permissions: newlyAddedDangerousPermissions,
      });
      return;
    }
    await saveRole(submission);
  });

  const submitting = form.formState.isSubmitting || createRole.isPending || updateRole.isPending;
  const selectedCount = [...checked].filter((code) => visibleCodes.has(code)).length;
  const selectedDangerousCount = visible.filter(
    (permission) => permission.is_dangerous && checked.has(permission.code),
  ).length;
  const initialPermissions = useMemo(() => new Set(role?.permissions ?? []), [role?.permissions]);
  const selectionDirty = !setsEqual(checked, initialPermissions);
  const editorDirty = form.formState.isDirty || selectionDirty;
  const selectedByGroup = useMemo(() => {
    const counts = new Map<string, number>();
    for (const permission of visible) {
      if (checked.has(permission.code)) {
        counts.set(permission.group_code, (counts.get(permission.group_code) ?? 0) + 1);
      }
    }
    return counts;
  }, [checked, visible]);
  const nameLength = form.watch("name").length;
  const descriptionLength = form.watch("description").length;

  useEffect(() => {
    onDirtyChange?.(editorDirty);
  }, [editorDirty, onDirtyChange]);

  return (
    <form onSubmit={onSubmit} noValidate className="min-w-0">
      <div className="grid min-w-0 gap-5 lg:grid-cols-[17rem_13rem_minmax(0,1fr)] lg:gap-0">
        <aside className="min-w-0 space-y-4 lg:border-r lg:border-border lg:pr-5">
          <div>
            <Label htmlFor="role-name">Название</Label>
            <Input
              id="role-name"
              disabled={editBlocked}
              invalid={Boolean(form.formState.errors.name)}
              aria-describedby={form.formState.errors.name ? "role-name-error" : undefined}
              placeholder="Например: Старший кассир"
              maxLength={100}
              {...form.register("name")}
            />
            <div className="mt-1.5 flex min-h-5 items-start justify-between gap-3">
              <FormError id="role-name-error">{form.formState.errors.name?.message}</FormError>
              <span className="ml-auto shrink-0 font-mono text-xs text-foreground-muted">
                {nameLength}/100
              </span>
            </div>
          </div>

          <div>
            <Label htmlFor="role-desc">Описание (необязательно)</Label>
            <Textarea
              id="role-desc"
              disabled={editBlocked}
              invalid={Boolean(form.formState.errors.description)}
              aria-describedby={
                form.formState.errors.description ? "role-description-error" : undefined
              }
              placeholder="Коротко, для чего эта роль"
              maxLength={500}
              {...form.register("description")}
            />
            <div className="mt-1.5 flex min-h-5 items-start justify-between gap-3">
              <FormError id="role-description-error">
                {form.formState.errors.description?.message}
              </FormError>
              <span className="ml-auto shrink-0 font-mono text-xs text-foreground-muted">
                {descriptionLength}/500
              </span>
            </div>
          </div>

          {mode === "create" && (
            <div>
              <Label htmlFor="role-template">Начать из шаблона (необязательно)</Label>
              <Select
                id="role-template"
                value={templateId}
                disabled={templatesQuery.isLoading || templatesQuery.isError}
                onChange={(event) => onTemplateChange(event.target.value)}
              >
                <option value="">— с нуля —</option>
                {templatesQuery.data?.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                  </option>
                ))}
              </Select>
              <p className="mt-1.5 text-xs leading-5 text-foreground-muted">
                Шаблон задаёт начальный набор. Любую доступную функцию можно изменить.
              </p>
              {templatesQuery.isLoading && (
                <p className="mt-1 text-xs text-foreground-muted">Загрузка шаблонов…</p>
              )}
              {templatesQuery.error && (
                <p className="mt-1 text-xs text-danger" role="alert">
                  {describeApiError(templatesQuery.error, "Не удалось загрузить шаблоны")}
                </p>
              )}
            </div>
          )}

          <div className="rounded-lg border border-border bg-background p-3" aria-live="polite">
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-foreground-secondary">Выбрано функций</span>
              <span className="font-mono text-lg font-semibold text-foreground">
                {selectedCount}
              </span>
            </div>
            {selectedDangerousCount > 0 && (
              <div className="mt-2 flex items-center justify-between gap-3 border-t border-danger/20 pt-2 text-sm text-danger-foreground">
                <span>Опасные функции</span>
                <Badge tone="danger">{selectedDangerousCount}</Badge>
              </div>
            )}
          </div>
        </aside>

        <nav
          aria-label="Разделы функций"
          className="hidden min-w-0 border-r border-border px-4 lg:block"
        >
          <p className="mb-2 text-xs font-semibold text-foreground-muted">Разделы</p>
          <div className="space-y-1">
            <button
              type="button"
              aria-pressed={activeGroup === "all"}
              aria-label={`Все функции: ${selectedCount} из ${visible.length} выбрано`}
              className={groupNavigationClass(activeGroup === "all")}
              onClick={() => setActiveGroup("all")}
            >
              <span>Все функции</span>
              <span className="font-mono text-xs tabular-nums">
                {selectedCount}/{visible.length}
              </span>
            </button>
            {catalogGroups.map((group) => {
              const selected = selectedByGroup.get(group.code) ?? 0;
              const label = groupLabel(group.code);
              return (
                <button
                  key={group.code}
                  type="button"
                  aria-pressed={activeGroup === group.code}
                  aria-label={`${label}: ${selected} из ${group.items.length} выбрано`}
                  className={groupNavigationClass(activeGroup === group.code)}
                  onClick={() => setActiveGroup(group.code)}
                >
                  <span className="truncate">{label}</span>
                  <span className="font-mono text-xs tabular-nums">
                    {selected}/{group.items.length}
                  </span>
                </button>
              );
            })}
          </div>
        </nav>

        <section
          className="min-w-0 lg:pl-5"
          aria-labelledby="role-functions-heading"
          data-testid="role-builder-workspace"
        >
          <div className="sticky top-0 z-10 -mx-1 mb-3 flex flex-wrap items-end justify-between gap-3 bg-surface-raised px-1 py-2 lg:static lg:mx-0 lg:bg-transparent lg:px-0 lg:py-0">
            <div>
              <h3 id="role-functions-heading" className="text-sm font-semibold text-foreground">
                Функции роли
              </h3>
              <p className="mt-0.5 text-xs text-foreground-muted">
                Показаны только функции, которые разрешено назначать вашему аккаунту.
              </p>
            </div>
            <div className="w-full sm:w-64">
              <Label htmlFor="permission-search" className="sr-only">
                Поиск функций
              </Label>
              <Input
                id="permission-search"
                type="search"
                value={permissionSearch}
                onChange={(event) => setPermissionSearch(event.target.value)}
                placeholder="Найти функцию"
              />
            </div>
            <div className="w-full lg:hidden">
              <Label htmlFor="permission-group">Раздел функций</Label>
              <Select
                id="permission-group"
                value={activeGroup}
                onChange={(event) => setActiveGroup(event.target.value)}
              >
                <option value="all">
                  Все функции ({selectedCount}/{visible.length})
                </option>
                {catalogGroups.map((group) => (
                  <option key={group.code} value={group.code}>
                    {groupLabel(group.code)} ({selectedByGroup.get(group.code) ?? 0}/
                    {group.items.length})
                  </option>
                ))}
              </Select>
            </div>
          </div>

          {editBlocked && (
            <p
              className="mb-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
              role="alert"
            >
              {ROLE_EDIT_BLOCKED_MESSAGE}
            </p>
          )}

          {permsQuery.isLoading ? (
            <p className="text-sm text-foreground-muted">Загрузка…</p>
          ) : permsQuery.error ? (
            <p className="text-sm text-danger" role="alert">
              {describeApiError(permsQuery.error, "Не удалось загрузить доступные функции")}
            </p>
          ) : groups.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-foreground-muted">
              {normalizedSearch ? "По этому запросу функций нет." : "Нет доступных функций."}
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              {groups.map((group) => {
                const allSelected = group.items.every((permission) => checked.has(permission.code));
                const selectedInGroup = group.items.filter((permission) =>
                  checked.has(permission.code),
                ).length;
                return (
                  <fieldset
                    key={group.code}
                    className="border-0 border-t border-border p-0 first:border-t-0"
                  >
                    <legend className="sr-only">{groupLabel(group.code)}: функции</legend>
                    <div className="flex items-center justify-between gap-3 bg-background px-3 py-2.5 sm:px-4">
                      <span className="flex min-w-0 items-center gap-2 text-sm font-semibold text-foreground">
                        <span className="truncate">{groupLabel(group.code)}</span>
                        <span className="shrink-0 font-mono text-xs font-normal text-foreground-muted">
                          {selectedInGroup}/{group.items.length}
                        </span>
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={editBlocked}
                        aria-pressed={allSelected ? true : selectedInGroup > 0 ? "mixed" : false}
                        onClick={() => toggleGroup(group.items)}
                      >
                        {allSelected
                          ? normalizedSearch
                            ? "Снять показанные"
                            : "Снять все"
                          : normalizedSearch
                            ? "Выбрать показанные"
                            : "Выбрать все"}
                      </Button>
                    </div>
                    <div className="divide-y divide-border">
                      {group.items.map((permission) => {
                        const descriptionId = `permission-${permission.code}-description`;
                        return (
                          <label
                            key={permission.code}
                            className={`flex cursor-pointer items-start gap-3 border-l-2 px-3 py-3 transition-colors duration-fast sm:px-4 ${
                              permission.is_dangerous
                                ? "border-l-danger bg-danger-subtle/35 hover:bg-danger-subtle/55"
                                : checked.has(permission.code)
                                  ? "border-l-primary bg-primary/[0.035]"
                                  : "border-l-transparent hover:bg-foreground/[0.025]"
                            }`}
                          >
                            <Checkbox
                              className="mt-0.5"
                              checked={checked.has(permission.code)}
                              disabled={editBlocked}
                              aria-describedby={permission.description ? descriptionId : undefined}
                              onChange={() => toggle(permission.code)}
                            />
                            <span className="min-w-0 flex-1 leading-tight">
                              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                                {permission.name}
                                <Badge tone="neutral">{scopeLabel(permission.scope_type)}</Badge>
                                {permission.is_dangerous && (
                                  <Badge tone="danger">опасное право</Badge>
                                )}
                              </span>
                              {permission.description && (
                                <span
                                  id={descriptionId}
                                  className="mt-1 block text-xs leading-5 text-foreground-muted"
                                >
                                  {permission.description}
                                </span>
                              )}
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {topError && (
        <p
          className="mt-4 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {topError}
        </p>
      )}

      <div className="sticky -bottom-4 z-sticky -mx-4 mt-5 flex flex-wrap items-center justify-end gap-2 border-t border-border bg-surface-raised px-4 pb-4 pt-3 sm:-mx-5 sm:px-5">
        <p className="mr-auto text-xs text-foreground-muted" aria-live="polite">
          Выбрано:{" "}
          <strong className="font-mono font-semibold text-foreground">{selectedCount}</strong> из{" "}
          {visible.length}
        </p>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button
          type="submit"
          isLoading={submitting}
          disabled={permsQuery.isLoading || permsQuery.isError || editBlocked}
        >
          {mode === "edit" ? "Сохранить" : "Создать роль"}
        </Button>
      </div>
      <ConfirmDialog
        open={pendingDangerousSubmission !== null}
        title="Подтвердите опасные функции"
        message={
          pendingDangerousSubmission ? (
            <div className="space-y-2">
              <p>Роль получит функции с повышенным риском:</p>
              <ul className="list-disc space-y-1 pl-5 text-foreground">
                {pendingDangerousSubmission.permissions.map((permission) => (
                  <li key={permission.code}>{permission.name}</li>
                ))}
              </ul>
            </div>
          ) : null
        }
        confirmLabel="Подтвердить и сохранить"
        variant="danger"
        isLoading={createRole.isPending || updateRole.isPending}
        onCancel={() => setPendingDangerousSubmission(null)}
        onConfirm={() => {
          if (!pendingDangerousSubmission) return;
          void saveRole(pendingDangerousSubmission.values);
        }}
      />
    </form>
  );
}

function groupNavigationClass(active: boolean): string {
  return [
    "flex min-h-10 w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors duration-fast",
    active
      ? "bg-primary text-primary-foreground"
      : "text-foreground-secondary hover:bg-foreground/5 hover:text-foreground",
  ].join(" ");
}

function scopeLabel(scope: Permission["scope_type"]): string {
  if (scope === "PLATFORM") return "Платформа";
  if (scope === "TENANT_ALL") return "Вся аптека";
  if (scope === "BRANCH_SET") return "По точкам";
  return "Только свои";
}

function setsEqual(left: Set<string>, right: Set<string>): boolean {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}
