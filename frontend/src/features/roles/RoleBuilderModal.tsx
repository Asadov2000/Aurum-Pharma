import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  Badge,
  Button,
  Checkbox,
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
}

const roleFormSchema = z.object({
  name: z.string().trim().min(1, "Введите название роли"),
  description: z.string(),
});

type RoleFormValues = z.infer<typeof roleFormSchema>;

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

export function RoleBuilderModal({ mode, role, onClose }: Props): JSX.Element {
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
  const [topError, setTopError] = useState<string | null>(null);

  // The server returns the grantable catalogue for this actor. The client must
  // neither expand it for support accounts nor infer delegation from JWT data.
  const visible = useMemo(() => permsQuery.data ?? [], [permsQuery.data]);
  const visibleCodes = useMemo(() => new Set(visible.map((p) => p.code)), [visible]);
  const normalizedSearch = permissionSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredVisible = useMemo(() => {
    if (!normalizedSearch) return visible;
    return visible.filter((permission) => {
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
  }, [normalizedSearch, visible]);
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
    setTopError(null);
    const codes = [...checked].filter((code) => visibleCodes.has(code));
    try {
      if (mode === "edit" && role) {
        await updateRole.mutateAsync({
          id: role.id,
          payload: {
            expected_version: role.version,
            name: parsed.data.name,
            description: parsed.data.description.trim() || null,
            permissions: codes,
          },
        });
      } else {
        await createRole.mutateAsync({
          name: parsed.data.name,
          description: parsed.data.description.trim() || null,
          permissions: codes,
        });
      }
      onClose();
    } catch (err) {
      setTopError(describeApiError(err, "Не удалось сохранить роль"));
    }
  });

  const submitting = form.formState.isSubmitting || createRole.isPending || updateRole.isPending;
  const selectedCount = [...checked].filter((code) => visibleCodes.has(code)).length;
  const selectedDangerousCount = visible.filter(
    (permission) => permission.is_dangerous && checked.has(permission.code),
  ).length;

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-5">
      <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(240px,0.72fr)_minmax(0,1.28fr)]">
        <div className="space-y-4">
          <div>
            <Label htmlFor="role-name">Название</Label>
            <Input
              id="role-name"
              disabled={editBlocked}
              invalid={Boolean(form.formState.errors.name)}
              aria-describedby={form.formState.errors.name ? "role-name-error" : undefined}
              placeholder="Например: Старший кассир"
              {...form.register("name")}
            />
            <FormError className="mt-1.5" id="role-name-error">
              {form.formState.errors.name?.message}
            </FormError>
          </div>

          <div>
            <Label htmlFor="role-desc">Описание (необязательно)</Label>
            <Textarea
              id="role-desc"
              disabled={editBlocked}
              placeholder="Коротко, для чего эта роль"
              {...form.register("description")}
            />
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

          <div className="rounded-lg border border-border bg-background p-3">
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
        </div>

        <section className="min-w-0" aria-labelledby="role-functions-heading">
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
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
            <div className="space-y-3 lg:max-h-[58vh] lg:overflow-y-auto lg:pr-1">
              {groups.map((group) => {
                const allSelected = group.items.every((permission) => checked.has(permission.code));
                return (
                  <fieldset
                    key={group.code}
                    className="rounded-lg border border-border bg-surface p-3"
                  >
                    <legend className="sr-only">{groupLabel(group.code)}: функции</legend>
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-foreground">
                        {groupLabel(group.code)}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={editBlocked}
                        onClick={() => toggleGroup(group.items)}
                      >
                        {allSelected ? "Снять все" : "Выбрать все"}
                      </Button>
                    </div>
                    <div className="space-y-1">
                      {group.items.map((permission) => {
                        const descriptionId = `permission-${permission.code}-description`;
                        return (
                          <label
                            key={permission.code}
                            className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors duration-fast ${
                              permission.is_dangerous
                                ? "border-danger/25 bg-danger-subtle/40 hover:border-danger/40"
                                : "border-transparent hover:bg-foreground/[0.03]"
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
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {topError}
        </p>
      )}

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        <Button type="button" variant="secondary" onClick={onClose}>
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
    </form>
  );
}
