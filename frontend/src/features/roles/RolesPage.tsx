import { useCallback, useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  ConfirmDialog,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
  Select,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/features/foundation/errors";

import { RoleBuilderModal } from "./RoleBuilderModal";
import { groupLabel } from "./labels";
import { usePermissionsQuery, useRolesQuery } from "./queries";
import {
  hasUnavailableRolePermissions,
  isManageableRole,
  ROLE_EDIT_BLOCKED_MESSAGE,
} from "./roleAccess";
import { type Role } from "./types";

type Editor = { mode: "create" } | { mode: "edit"; role: Role } | null;
type RoleStatusFilter = "all" | "active" | "inactive";

export function RolesPage(): JSX.Element {
  const { user } = useAuth();
  const filterPreferenceKey = useFilterPreferenceKey("roles");
  const tenantId = activeTenantId(user);
  const hasTenant = Boolean(tenantId);
  const isDeveloper = user?.is_developer === true;
  const isSupport = isDeveloper || user?.is_administrator === true;
  const isSupportScoped = user?.support_access !== null && user?.support_access !== undefined;
  const userPermissions = user?.permissions ?? [];
  const developerBypass = isDeveloper && !isSupportScoped;
  const canCreate = developerBypass || userPermissions.includes("roles.create");
  const canUpdate = developerBypass || userPermissions.includes("roles.update");
  const canAssign = developerBypass || userPermissions.includes("roles.assign");
  const canManageRoles = canCreate || canUpdate || canAssign;
  const canView =
    hasTenant &&
    ((isSupportScoped && isSupport && canManageRoles) ||
      (user?.is_tenant_owner === true && canManageRoles));

  const roles = useRolesQuery(canView);
  const perms = usePermissionsQuery(canView);
  const [editor, setEditor] = useState<Editor>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);
  const [roleSearch, setRoleSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<RoleStatusFilter>("all");

  const closeEditor = useCallback(() => {
    setDiscardConfirmationOpen(false);
    setEditorDirty(false);
    setEditor(null);
  }, []);

  const requestEditorClose = useCallback(() => {
    if (editorDirty) {
      setDiscardConfirmationOpen(true);
      return;
    }
    closeEditor();
  }, [closeEditor, editorDirty]);

  const permName = useMemo(() => {
    const m = new Map<string, string>();
    perms.data?.forEach((p) => m.set(p.code, p.name));
    return m;
  }, [perms.data]);

  if (!canView) {
    return (
      <AccessDeniedCard title="Роли" message="У вас нет доступа к управлению ролями этой аптеки." />
    );
  }

  if (roles.error) {
    return (
      <div className="space-y-4">
        <PageHeader title="Роли" />
        <p className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground">
          {describeApiError(roles.error, "Не удалось загрузить список")}
        </p>
      </div>
    );
  }

  const tenantRoles = (roles.data ?? []).filter((role) => isManageableRole(role, tenantId));
  const normalizedRoleSearch = roleSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredTenantRoles = tenantRoles.filter((role) => {
    if (statusFilter === "active" && !role.is_active) return false;
    if (statusFilter === "inactive" && role.is_active) return false;
    if (!normalizedRoleSearch) return true;
    return [role.name, role.description ?? ""]
      .join(" ")
      .toLocaleLowerCase("ru-RU")
      .includes(normalizedRoleSearch);
  });

  const grantableCodes = new Set(permName.keys());

  const roleRow = (r: Role) => {
    const visiblePermissions = r.permissions.filter((code) => grantableCodes.has(code));
    const editBlocked = perms.isSuccess && hasUnavailableRolePermissions(r);
    const groupCounts = new Map<string, number>();
    for (const permission of perms.data ?? []) {
      if (!visiblePermissions.includes(permission.code)) continue;
      groupCounts.set(permission.group_code, (groupCounts.get(permission.group_code) ?? 0) + 1);
    }
    const roleGroups = [...groupCounts.entries()];
    const shownGroups = roleGroups.slice(0, 5);
    const hiddenGroupCount = Math.max(0, roleGroups.length - shownGroups.length);

    return (
      <li
        key={r.id}
        className="grid min-w-0 gap-4 px-4 py-4 md:grid-cols-[minmax(13rem,1.1fr)_minmax(16rem,1.6fr)_7rem_7rem] md:items-center xl:px-5"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-primary/10 font-display text-sm font-semibold text-primary"
            >
              {r.name.charAt(0).toLocaleUpperCase("ru-RU")}
            </span>
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-foreground">{r.name}</h3>
              <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-foreground-secondary">
                {r.description || "Описание роли не задано."}
              </p>
            </div>
          </div>
        </div>

        <div className="min-w-0">
          <span className="mb-1.5 block text-xs font-medium text-foreground-muted md:hidden">
            Доступные функции
          </span>
          <div className="flex min-w-0 flex-wrap gap-1.5">
            {perms.isLoading ? (
              <span className="text-xs italic text-foreground-muted">загрузка функций…</span>
            ) : perms.isError ? (
              <span className="text-xs text-danger-foreground">данные недоступны</span>
            ) : shownGroups.length === 0 ? (
              <span className="text-xs italic text-foreground-muted">нет доступных функций</span>
            ) : (
              shownGroups.map(([code, count]) => (
                <Badge key={code} tone="neutral">
                  {groupLabel(code)} · {count}
                </Badge>
              ))
            )}
            {hiddenGroupCount > 0 && <Badge tone="neutral">ещё {hiddenGroupCount}</Badge>}
          </div>
          {editBlocked && (
            <p className="mt-2 text-xs leading-5 text-warning-foreground">
              Некоторые функции этой роли недоступны для изменения.
            </p>
          )}
        </div>

        <div>
          <span className="mb-1 block text-xs font-medium text-foreground-muted md:hidden">
            Всего функций
          </span>
          <span className="font-mono text-sm font-semibold tabular-nums text-foreground">
            {perms.isSuccess ? visiblePermissions.length : "—"}
          </span>
        </div>

        <div className="flex items-center justify-between gap-3 md:justify-end">
          <Badge tone={r.is_active ? "success" : "neutral"}>
            {r.is_active ? "активна" : "неактивна"}
          </Badge>
          {canUpdate && (
            <Button
              variant="secondary"
              size="sm"
              disabled={!perms.isSuccess || editBlocked}
              title={editBlocked ? ROLE_EDIT_BLOCKED_MESSAGE : undefined}
              onClick={() => {
                setEditorDirty(false);
                setEditor({ mode: "edit", role: r });
              }}
            >
              Изменить
            </Button>
          )}
        </div>
      </li>
    );
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Роли"
        meta={
          !roles.isLoading
            ? filteredTenantRoles.length === tenantRoles.length
              ? `Всего: ${tenantRoles.length}`
              : `Показано: ${filteredTenantRoles.length} из ${tenantRoles.length}`
            : undefined
        }
        actions={
          canCreate ? (
            <Button
              aria-label="+ Создать роль"
              onClick={() => {
                setEditorDirty(false);
                setEditor({ mode: "create" });
              }}
            >
              <PlusIcon />
              Создать роль
            </Button>
          ) : undefined
        }
      />

      <ConfigurableFilterBar
        preferenceKey={filterPreferenceKey}
        filters={[
          {
            id: "search",
            label: "Поиск",
            content: (
              <div>
                <Label htmlFor="role-search">Поиск</Label>
                <Input
                  id="role-search"
                  type="search"
                  value={roleSearch}
                  onChange={(event) => setRoleSearch(event.target.value)}
                  placeholder="Название или описание"
                />
              </div>
            ),
            active: Boolean(roleSearch),
            onClear: () => setRoleSearch(""),
            alwaysVisible: true,
          },
          {
            id: "status",
            label: "Статус",
            content: (
              <div>
                <Label htmlFor="role-status">Статус</Label>
                <Select
                  id="role-status"
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value as RoleStatusFilter)}
                  className="w-full sm:w-44"
                >
                  <option value="all">Все роли</option>
                  <option value="active">Активные</option>
                  <option value="inactive">Неактивные</option>
                </Select>
              </div>
            ),
            active: statusFilter !== "all",
            onClear: () => setStatusFilter("all"),
            defaultVisible: true,
          },
        ]}
        onResetValues={() => {
          setRoleSearch("");
          setStatusFilter("all");
        }}
      />

      {perms.error && (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {describeApiError(perms.error, "Не удалось загрузить названия функций")}
        </p>
      )}

      {roles.isLoading ? (
        <SkeletonRows rows={4} />
      ) : (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-foreground-secondary">Роли аптеки</h2>
            <div className="flex items-center gap-4 text-xs text-foreground-muted">
              <span>
                Активные{" "}
                <strong className="font-mono font-semibold text-foreground">
                  {tenantRoles.filter((role) => role.is_active).length}
                </strong>
              </span>
              <span>
                Доступные функции{" "}
                <strong className="font-mono font-semibold text-foreground">
                  {perms.isSuccess ? perms.data.length : "—"}
                </strong>
              </span>
            </div>
          </div>
          {filteredTenantRoles.length === 0 ? (
            <TableEmpty title={tenantRoles.length > 0 ? "Роли не найдены" : "Роли пока не созданы"}>
              {tenantRoles.length > 0
                ? "По выбранным фильтрам ролей нет."
                : canCreate
                  ? "Создайте первую роль или начните с готового шаблона."
                  : "Управляемых ролей пока нет."}
            </TableEmpty>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border bg-surface">
              <div className="hidden grid-cols-[minmax(13rem,1.1fr)_minmax(16rem,1.6fr)_7rem_7rem] gap-4 border-b border-border bg-background px-5 py-2.5 text-xs font-semibold text-foreground-muted md:grid">
                <span>Роль</span>
                <span>Функции</span>
                <span>Всего</span>
                <span className="text-right">Статус</span>
              </div>
              <ul className="divide-y divide-border">{filteredTenantRoles.map(roleRow)}</ul>
            </div>
          )}
        </section>
      )}

      <Modal
        open={editor !== null}
        onClose={requestEditorClose}
        title={editor?.mode === "edit" ? "Изменить роль" : "Создать роль"}
        className="max-w-[78rem]"
      >
        {editor && (
          <RoleBuilderModal
            mode={editor.mode}
            role={editor.mode === "edit" ? editor.role : undefined}
            onClose={closeEditor}
            onCancel={requestEditorClose}
            onDirtyChange={setEditorDirty}
          />
        )}
      </Modal>
      <ConfirmDialog
        open={discardConfirmationOpen}
        title="Отменить изменения?"
        message="Внесённые изменения роли не сохранятся."
        cancelLabel="Продолжить редактирование"
        confirmLabel="Выйти без сохранения"
        variant="danger"
        onCancel={() => setDiscardConfirmationOpen(false)}
        onConfirm={closeEditor}
      />
    </div>
  );
}

function PlusIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
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
