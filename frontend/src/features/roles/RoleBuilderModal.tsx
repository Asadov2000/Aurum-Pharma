import { useDeferredValue, useEffect, useMemo, useState } from "react";
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
  SegmentedControl,
  Select,
  SkeletonRows,
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
type CatalogueView = "all" | "selected" | "risk";
type MobilePane = "details" | "permissions";

interface RoleSubmission {
  name: string;
  description: string | null;
  permissions: string[];
}

interface PendingDangerousSubmission {
  values: RoleSubmission;
  permissions: Permission[];
}

interface PermissionGroup {
  code: string;
  items: Permission[];
}

const EMPTY_PERMISSIONS: readonly Permission[] = [];

function groupBy(perms: readonly Permission[]): PermissionGroup[] {
  const order: string[] = [];
  const byGroup = new Map<string, Permission[]>();
  for (const permission of perms) {
    if (!byGroup.has(permission.group_code)) {
      byGroup.set(permission.group_code, []);
      order.push(permission.group_code);
    }
    byGroup.get(permission.group_code)?.push(permission);
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
  const [pendingTemplateId, setPendingTemplateId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(() => new Set(role?.permissions ?? []));
  const [permissionSearch, setPermissionSearch] = useState("");
  const [activeGroup, setActiveGroup] = useState("all");
  const [catalogueView, setCatalogueView] = useState<CatalogueView>("all");
  const [mobilePane, setMobilePane] = useState<MobilePane>("details");
  const [topError, setTopError] = useState<string | null>(null);
  const [pendingDangerousSubmission, setPendingDangerousSubmission] =
    useState<PendingDangerousSubmission | null>(null);
  const deferredPermissionSearch = useDeferredValue(permissionSearch);

  // The server catalogue is authoritative. The extra client-side filter keeps
  // the editor closed if a malformed response ever includes an unusable item.
  const visible = useMemo(
    () =>
      (permsQuery.data ?? EMPTY_PERMISSIONS).filter(
        (permission) =>
          permission.is_active &&
          permission.target_role_type === "tenant" &&
          permission.scope_type !== "PLATFORM",
      ),
    [permsQuery.data],
  );
  const visibleCodes = useMemo(
    () => new Set(visible.map((permission) => permission.code)),
    [visible],
  );
  const catalogGroups = useMemo(() => groupBy(visible), [visible]);
  const normalizedSearch = deferredPermissionSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredVisible = useMemo(
    () =>
      visible.filter((permission) => {
        if (activeGroup !== "all" && permission.group_code !== activeGroup) return false;
        if (catalogueView === "selected" && !checked.has(permission.code)) return false;
        if (catalogueView === "risk" && !isRiskPermission(permission)) return false;
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
      }),
    [activeGroup, catalogueView, checked, normalizedSearch, visible],
  );
  const groups = useMemo(() => groupBy(filteredVisible), [filteredVisible]);
  const containsUnknownRolePermission =
    mode === "edit" &&
    role !== undefined &&
    permsQuery.isSuccess &&
    role.permissions.some((code) => !visibleCodes.has(code));
  const editBlocked =
    mode === "edit" &&
    role !== undefined &&
    permsQuery.isSuccess &&
    (hasUnavailableRolePermissions(role) || containsUnknownRolePermission);

  const toggle = (code: string) => {
    setChecked((previous) => {
      const next = new Set(previous);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const toggleGroup = (items: readonly Permission[]) => {
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

  const applyTemplate = (id: string) => {
    setTemplateId(id);
    const template = templatesQuery.data?.find((item) => item.id === id);
    setChecked(
      new Set(template ? template.permissions.filter((code) => visibleCodes.has(code)) : []),
    );
    setPendingTemplateId(null);
    setTopError(null);
  };

  const onTemplateChange = (id: string) => {
    if (id === templateId) return;
    if (checked.size > 0) {
      setPendingTemplateId(id);
      return;
    }
    applyTemplate(id);
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
    } catch (error) {
      setPendingDangerousSubmission(null);
      setTopError(describeApiError(error, "Не удалось сохранить роль"));
    }
  };

  const onSubmit = form.handleSubmit(async (values) => {
    form.clearErrors();
    if (!permsQuery.isSuccess || createRole.isPending || updateRole.isPending) {
      setTopError("Дождитесь загрузки доступных функций и повторите сохранение.");
      return;
    }
    if (editBlocked) {
      setTopError(ROLE_EDIT_BLOCKED_MESSAGE);
      return;
    }
    if (
      mode === "edit" &&
      !form.formState.isDirty &&
      setsEqual(checked, new Set(role?.permissions ?? []))
    ) {
      return;
    }
    const parsed = roleFormSchema.safeParse(values);
    if (!parsed.success) {
      let firstInvalidField: keyof RoleFormValues | null = null;
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (field === "name" || field === "description") {
          firstInvalidField ??= field;
          form.setError(field, { message: issue.message });
        }
      }
      setMobilePane("details");
      if (firstInvalidField) form.setFocus(firstInvalidField);
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
        requiresExplicitConfirmation(permission) &&
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
  const selectedPermissions = useMemo(
    () => visible.filter((permission) => checked.has(permission.code)),
    [checked, visible],
  );
  const selectedCount = selectedPermissions.length;
  const selectedRiskCount = selectedPermissions.filter(isRiskPermission).length;
  const selectedConfirmationCount = selectedPermissions.filter(requiresExplicitConfirmation).length;
  const initialPermissions = useMemo(() => new Set(role?.permissions ?? []), [role?.permissions]);
  const selectionDirty = !setsEqual(checked, initialPermissions);
  const editorDirty = form.formState.isDirty || selectionDirty;
  const addedCount = selectedPermissions.filter(
    (permission) => !initialPermissions.has(permission.code),
  ).length;
  const removedCount = [...initialPermissions].filter(
    (code) => visibleCodes.has(code) && !checked.has(code),
  ).length;
  const selectedByGroup = useMemo(() => {
    const counts = new Map<string, number>();
    for (const permission of selectedPermissions) {
      counts.set(permission.group_code, (counts.get(permission.group_code) ?? 0) + 1);
    }
    return counts;
  }, [selectedPermissions]);
  const selectedGroupCount = selectedByGroup.size;
  const nameLength = form.watch("name").length;
  const descriptionLength = form.watch("description").length;
  const catalogueFiltered = Boolean(normalizedSearch || catalogueView !== "all");
  const saveDisabled =
    permsQuery.isLoading ||
    permsQuery.isError ||
    editBlocked ||
    submitting ||
    (mode === "edit" && !editorDirty);

  useEffect(() => {
    onDirtyChange?.(editorDirty);
  }, [editorDirty, onDirtyChange]);

  return (
    <form
      onSubmit={onSubmit}
      onKeyDown={(event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault();
          if (!saveDisabled) event.currentTarget.requestSubmit();
        }
      }}
      noValidate
      className="flex h-full min-h-0 min-w-0 flex-col"
    >
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        <div className="mb-4 xl:hidden">
          <SegmentedControl
            value={mobilePane}
            label="Раздел конструктора"
            size="lg"
            className="grid w-full grid-cols-2"
            options={[
              { value: "details", label: "О роли" },
              { value: "permissions", label: `Права доступа · ${selectedCount}` },
            ]}
            onChange={setMobilePane}
          />
        </div>

        <div className="mb-5 hidden overflow-hidden rounded-lg border border-border bg-background xl:grid xl:grid-cols-4 xl:divide-x xl:divide-border">
          <BuilderMetric
            label="Режим"
            value={mode === "edit" ? `Версия ${role?.version ?? 1}` : "Новая роль"}
          />
          <BuilderMetric label="Выбрано функций" value={selectedCount} />
          <BuilderMetric label="Разделов" value={selectedGroupCount} />
          <BuilderMetric
            label="С подтверждением"
            value={selectedConfirmationCount}
            tone={selectedConfirmationCount > 0 ? "warning" : "neutral"}
          />
        </div>

        <div className="grid min-w-0 gap-5 xl:grid-cols-[18rem_14rem_minmax(0,1fr)] xl:gap-0">
          <aside
            className={`${mobilePane === "details" ? "block" : "hidden"} min-w-0 space-y-4 xl:block xl:border-r xl:border-border xl:pr-5`}
            aria-label="Основные данные роли"
          >
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-foreground">Основные данные</h3>
              {mode === "edit" ? <Badge tone="neutral">версия {role?.version}</Badge> : null}
            </div>

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

            {mode === "create" ? (
              <div>
                <Label htmlFor="role-template">Начать из шаблона (необязательно)</Label>
                <Select
                  id="role-template"
                  value={templateId}
                  disabled={templatesQuery.isLoading || templatesQuery.isError}
                  onChange={(event) => onTemplateChange(event.target.value)}
                >
                  <option value="">С нуля</option>
                  {templatesQuery.data
                    ?.filter((template) => template.is_active)
                    .map((template) => (
                      <option key={template.id} value={template.id}>
                        {template.name}
                      </option>
                    ))}
                </Select>
                <p className="mt-1.5 text-xs leading-5 text-foreground-muted">
                  Шаблон задаёт начальный набор доступных функций.
                </p>
                {templatesQuery.isLoading ? (
                  <p className="mt-1 text-xs text-foreground-muted">Загрузка шаблонов…</p>
                ) : null}
                {templatesQuery.error ? (
                  <p className="mt-1 text-xs text-danger" role="alert">
                    {describeApiError(templatesQuery.error, "Не удалось загрузить шаблоны")}
                  </p>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-2 border-t border-border pt-4" aria-live="polite">
              <h4 className="text-xs font-semibold text-foreground-muted">Сводка доступа</h4>
              <SummaryRow label="Функции" value={selectedCount} />
              <SummaryRow label="Разделы" value={selectedGroupCount} />
              <SummaryRow
                label="Повышенный риск"
                value={selectedRiskCount}
                tone={selectedRiskCount > 0 ? "warning" : "neutral"}
              />
              {editorDirty ? (
                <div className="flex items-center justify-between gap-3 border-t border-border pt-2 text-xs">
                  <span className="text-foreground-muted">Изменения функций</span>
                  <span className="font-mono font-semibold tabular-nums text-foreground">
                    <span className="text-success">+{addedCount}</span> /{" "}
                    <span className="text-danger">−{removedCount}</span>
                  </span>
                </div>
              ) : null}
            </div>
          </aside>

          <nav
            aria-label="Разделы функций"
            className="hidden min-w-0 border-r border-border px-4 xl:block"
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
            className={`${mobilePane === "permissions" ? "block" : "hidden"} min-w-0 xl:block xl:pl-5`}
            aria-labelledby="role-functions-heading"
            data-testid="role-builder-workspace"
          >
            <div className="sticky top-0 z-10 -mx-1 mb-3 space-y-3 bg-surface-raised px-1 pb-2 xl:static xl:mx-0 xl:bg-transparent xl:px-0 xl:pb-0">
              <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3
                      id="role-functions-heading"
                      className="text-sm font-semibold text-foreground"
                    >
                      Функции роли
                    </h3>
                    <Badge tone="neutral">{filteredVisible.length}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-foreground-muted">
                    Отображается только доступный вашему аккаунту каталог.
                  </p>
                </div>
                <div className="w-full sm:w-64">
                  <Label htmlFor="permission-search" className="sr-only">
                    Поиск функций
                  </Label>
                  <Input
                    id="permission-search"
                    type="search"
                    autoComplete="off"
                    value={permissionSearch}
                    onChange={(event) => setPermissionSearch(event.target.value)}
                    placeholder="Найти функцию"
                  />
                </div>
              </div>

              <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
                <SegmentedControl
                  value={catalogueView}
                  label="Фильтр функций"
                  size="sm"
                  className="grid min-w-0 flex-1 grid-cols-3 sm:flex-none"
                  options={[
                    { value: "all", label: `Все · ${visible.length}` },
                    { value: "selected", label: `Выбрано · ${selectedCount}` },
                    {
                      value: "risk",
                      label: `Риск · ${visible.filter(isRiskPermission).length}`,
                    },
                  ]}
                  onChange={setCatalogueView}
                />
                <div className="w-full xl:hidden">
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
            </div>

            {editBlocked ? (
              <p
                className="mb-3 rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
                role="alert"
              >
                {ROLE_EDIT_BLOCKED_MESSAGE}
              </p>
            ) : null}

            {permsQuery.isLoading ? (
              <SkeletonRows rows={5} />
            ) : permsQuery.error ? (
              <p className="text-sm text-danger" role="alert">
                {describeApiError(permsQuery.error, "Не удалось загрузить доступные функции")}
              </p>
            ) : groups.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-foreground-muted">
                {emptyCatalogueMessage(catalogueView, normalizedSearch)}
              </div>
            ) : (
              <div className="overflow-hidden rounded-lg border border-border bg-surface">
                {groups.map((group) => {
                  const allSelected = group.items.every((permission) =>
                    checked.has(permission.code),
                  );
                  const selectedInGroup = group.items.filter((permission) =>
                    checked.has(permission.code),
                  ).length;
                  return (
                    <fieldset
                      key={group.code}
                      className="border-0 border-t border-border p-0 first:border-t-0"
                    >
                      <legend className="sr-only">{groupLabel(group.code)}: функции</legend>
                      <div className="flex min-h-12 items-center justify-between gap-3 bg-background px-3 py-2.5 sm:px-4">
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
                            ? catalogueFiltered
                              ? "Снять показанные"
                              : "Снять все"
                            : catalogueFiltered
                              ? "Выбрать показанные"
                              : "Выбрать все"}
                        </Button>
                      </div>
                      <div className="divide-y divide-border">
                        {group.items.map((permission) => {
                          const descriptionId = `permission-${permission.code}-description`;
                          const selected = checked.has(permission.code);
                          return (
                            <label
                              key={permission.code}
                              className={permissionRowClass(permission, selected)}
                            >
                              <Checkbox
                                className="mt-0.5"
                                checked={selected}
                                disabled={editBlocked}
                                aria-describedby={
                                  permission.description ? descriptionId : undefined
                                }
                                onChange={() => toggle(permission.code)}
                              />
                              <span className="min-w-0 flex-1 leading-tight">
                                <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                                  {permission.name}
                                  <Badge tone="neutral">{scopeLabel(permission.scope_type)}</Badge>
                                  {permission.risk_level !== "normal" || permission.is_dangerous ? (
                                    <Badge tone={permissionRiskTone(permission)}>
                                      {permissionRiskLabel(permission)}
                                    </Badge>
                                  ) : null}
                                  {permission.requires_step_up ? (
                                    <Badge tone="info" title="MFA при использовании функции">
                                      MFA
                                    </Badge>
                                  ) : null}
                                  {permission.requires_confirmation && !permission.is_dangerous ? (
                                    <Badge tone="warning">подтверждение</Badge>
                                  ) : null}
                                </span>
                                {permission.description ? (
                                  <span
                                    id={descriptionId}
                                    className="mt-1 block text-xs leading-5 text-foreground-muted"
                                  >
                                    {permission.description}
                                  </span>
                                ) : null}
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

      </div>

      {topError ? (
        <p
          className="-mx-4 mt-3 shrink-0 border-y border-danger/30 bg-danger-subtle px-4 py-2 text-sm text-danger-foreground sm:-mx-5 sm:px-5"
          role="alert"
        >
          {topError}
        </p>
      ) : null}

      <div className="-mx-4 -mb-4 mt-4 flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-surface-raised px-4 pb-4 pt-3 sm:-mx-5 sm:px-5">
        <p className="mr-auto text-xs text-foreground-muted" aria-live="polite">
          {editorDirty ? (
            <>
              Изменения: <strong className="font-mono text-success">+{addedCount}</strong> /{" "}
              <strong className="font-mono text-danger">−{removedCount}</strong>
            </>
          ) : (
            <>
              Выбрано: <strong className="font-mono text-foreground">{selectedCount}</strong> из{" "}
              {visible.length}
            </>
          )}
        </p>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" isLoading={submitting} disabled={saveDisabled}>
          {mode === "edit" ? "Сохранить" : "Создать роль"}
        </Button>
      </div>

      <ConfirmDialog
        open={pendingTemplateId !== null}
        title="Заменить выбранные функции?"
        message="Текущий набор будет заменён функциями выбранного шаблона. Название и описание роли сохранятся."
        confirmLabel="Применить шаблон"
        onCancel={() => setPendingTemplateId(null)}
        onConfirm={() => {
          if (pendingTemplateId === null) return;
          applyTemplate(pendingTemplateId);
        }}
      />
      <ConfirmDialog
        open={pendingDangerousSubmission !== null}
        title="Подтвердите расширение доступа"
        message={
          pendingDangerousSubmission ? (
            <div className="space-y-2">
              <p>Роль получит функции повышенного риска:</p>
              <ul className="list-disc space-y-1 pl-5 text-foreground">
                {pendingDangerousSubmission.permissions.map((permission) => (
                  <li key={permission.code}>
                    {permission.name}
                    {permission.requires_step_up ? " — MFA при использовании" : null}
                  </li>
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

function BuilderMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  tone?: "neutral" | "warning";
}): JSX.Element {
  return (
    <div className="min-w-0 px-4 py-3">
      <span className="block text-xs font-medium text-foreground-muted">{label}</span>
      <strong
        className={`mt-0.5 block truncate text-sm font-semibold ${tone === "warning" ? "text-warning-foreground" : "text-foreground"}`}
      >
        {value}
      </strong>
    </div>
  );
}

function SummaryRow({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "warning";
}): JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="text-foreground-secondary">{label}</span>
      <span
        className={`font-mono font-semibold tabular-nums ${tone === "warning" ? "text-warning-foreground" : "text-foreground"}`}
      >
        {value}
      </span>
    </div>
  );
}

function groupNavigationClass(active: boolean): string {
  return [
    "flex min-h-10 w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors duration-fast",
    active
      ? "bg-primary/10 font-semibold text-primary"
      : "text-foreground-secondary hover:bg-foreground/5 hover:text-foreground",
  ].join(" ");
}

function permissionRowClass(permission: Permission, selected: boolean): string {
  const base =
    "flex min-h-14 cursor-pointer items-start gap-3 border-l-2 px-3 py-3 transition-colors duration-fast sm:px-4";
  if (permission.risk_level === "critical" || permission.is_dangerous) {
    return `${base} border-l-danger bg-danger-subtle/35 hover:bg-danger-subtle/55`;
  }
  if (permission.risk_level === "sensitive" || permission.requires_confirmation) {
    return `${base} border-l-warning bg-warning-subtle/25 hover:bg-warning-subtle/45`;
  }
  return selected
    ? `${base} border-l-primary bg-primary/[0.04]`
    : `${base} border-l-transparent hover:bg-foreground/[0.025]`;
}

function scopeLabel(scope: Permission["scope_type"]): string {
  if (scope === "PLATFORM") return "Платформа";
  if (scope === "TENANT_ALL") return "Вся аптека";
  if (scope === "BRANCH_SET") return "По точкам";
  return "Только свои";
}

function isRiskPermission(permission: Permission): boolean {
  return (
    permission.is_dangerous ||
    permission.risk_level !== "normal" ||
    permission.requires_confirmation ||
    permission.requires_step_up
  );
}

function requiresExplicitConfirmation(permission: Permission): boolean {
  return (
    permission.is_dangerous || permission.requires_confirmation || permission.requires_step_up
  );
}

function permissionRiskLabel(permission: Permission): string {
  if (permission.risk_level === "critical" || permission.is_dangerous) return "критично";
  return "повышенный риск";
}

function permissionRiskTone(permission: Permission): "danger" | "warning" {
  return permission.risk_level === "critical" || permission.is_dangerous ? "danger" : "warning";
}

function emptyCatalogueMessage(view: CatalogueView, normalizedSearch: string): string {
  if (normalizedSearch) return "По этому запросу функций нет.";
  if (view === "selected") return "В этом разделе пока ничего не выбрано.";
  if (view === "risk") return "В этом разделе нет функций повышенного риска.";
  return "Нет доступных функций.";
}

function setsEqual(left: ReadonlySet<string>, right: ReadonlySet<string>): boolean {
  if (left.size !== right.size) return false;
  for (const value of left) {
    if (!right.has(value)) return false;
  }
  return true;
}
