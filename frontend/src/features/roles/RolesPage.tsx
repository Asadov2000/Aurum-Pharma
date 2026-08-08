import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useDeferredValue,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  Card,
  CardContent,
  ConfirmDialog,
  ConfigurableFilterBar,
  Input,
  Label,
  Modal,
  PageHeader,
  Pagination,
  Select,
  Skeleton,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
import { useFilterPreferenceKey } from "@/features/auth/filterPreferences";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { describeApiError } from "@/features/foundation/errors";

import { groupLabel } from "./labels";
import { usePermissionsQuery, useRolesQuery } from "./queries";
import {
  hasUnavailableRolePermissions,
  isManageableRole,
  ROLE_EDIT_BLOCKED_MESSAGE,
} from "./roleAccess";
import { type Permission, type Role } from "./types";

type Editor = { mode: "create" } | { mode: "edit"; role: Role } | null;
type RoleStatusFilter = "all" | "active" | "inactive";

interface RoleView {
  role: Role;
  permissionCount: number;
  groups: Array<{ code: string; count: number }>;
  editBlocked: boolean;
}

const ROLE_PAGE_SIZE = 8;
const EMPTY_ROLES: readonly Role[] = [];
const EMPTY_PERMISSIONS: readonly Permission[] = [];
const RoleBuilderModal = lazy(async () => {
  const module = await import("./RoleBuilderModal");
  return { default: module.RoleBuilderModal };
});

interface RoleBuilderLoadBoundaryProps {
  children: ReactNode;
  onClose: () => void;
}

interface RoleBuilderLoadBoundaryState {
  failed: boolean;
}

export class RoleBuilderLoadBoundary extends Component<
  RoleBuilderLoadBoundaryProps,
  RoleBuilderLoadBoundaryState
> {
  override state: RoleBuilderLoadBoundaryState = { failed: false };

  static getDerivedStateFromError(): RoleBuilderLoadBoundaryState {
    return { failed: true };
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;

    return (
      <div
        className="grid min-h-56 place-items-center rounded-lg border border-warning/30 bg-warning-subtle p-6 text-center"
        role="alert"
      >
        <div className="max-w-md">
          <h3 className="text-base font-semibold text-foreground">Конструктор не загрузился</h3>
          <p className="mt-2 text-sm leading-6 text-foreground-secondary">
            Не удалось загрузить файл интерфейса. Проверьте подключение и обновите страницу.
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <Button variant="secondary" onClick={this.props.onClose}>
              Закрыть
            </Button>
            <Button onClick={() => window.location.reload()}>Обновить страницу</Button>
          </div>
        </div>
      </div>
    );
  }
}

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
  const canUseBuilder = canCreate || canUpdate;
  const canView =
    hasTenant &&
    ((isSupportScoped && isSupport && canManageRoles) ||
      (user?.is_tenant_owner === true && canManageRoles));

  const roles = useRolesQuery(canView);
  // Assignment-only users need role names, not the constructor catalogue.
  const perms = usePermissionsQuery(canView && canUseBuilder);
  const [editor, setEditor] = useState<Editor>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);
  const [roleSearch, setRoleSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<RoleStatusFilter>("all");
  const [rolePage, setRolePage] = useState(1);
  const deferredRoleSearch = useDeferredValue(roleSearch);

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

  const safePermissions = useMemo(
    () =>
      canUseBuilder
        ? (perms.data ?? EMPTY_PERMISSIONS).filter(
            (permission) =>
              permission.is_active &&
              permission.target_role_type === "tenant" &&
              permission.scope_type !== "PLATFORM",
          )
        : EMPTY_PERMISSIONS,
    [canUseBuilder, perms.data],
  );
  const permissionByCode = useMemo(
    () => new Map(safePermissions.map((permission) => [permission.code, permission])),
    [safePermissions],
  );
  const tenantRoles = useMemo(
    () => (roles.data ?? EMPTY_ROLES).filter((role) => isManageableRole(role, tenantId)),
    [roles.data, tenantId],
  );
  const roleViews = useMemo(
    () =>
      tenantRoles.map((role) =>
        buildRoleView(role, permissionByCode, canUseBuilder, perms.isSuccess),
      ),
    [canUseBuilder, permissionByCode, perms.isSuccess, tenantRoles],
  );

  const normalizedRoleSearch = deferredRoleSearch.trim().toLocaleLowerCase("ru-RU");
  const filteredRoleViews = useMemo(
    () =>
      roleViews.filter(({ role }) => {
        if (statusFilter === "active" && !role.is_active) return false;
        if (statusFilter === "inactive" && role.is_active) return false;
        if (!normalizedRoleSearch) return true;
        return [role.name, role.description ?? ""]
          .join(" ")
          .toLocaleLowerCase("ru-RU")
          .includes(normalizedRoleSearch);
      }),
    [normalizedRoleSearch, roleViews, statusFilter],
  );
  const totalPages = Math.max(1, Math.ceil(filteredRoleViews.length / ROLE_PAGE_SIZE));
  const visibleRolePage = Math.min(rolePage, totalPages);
  const pagedRoleViews = useMemo(
    () =>
      filteredRoleViews.slice(
        (visibleRolePage - 1) * ROLE_PAGE_SIZE,
        visibleRolePage * ROLE_PAGE_SIZE,
      ),
    [filteredRoleViews, visibleRolePage],
  );
  const activeRoleCount = tenantRoles.filter((role) => role.is_active).length;
  const confirmationPermissionCount = safePermissions.filter(
    (permission) => permission.is_dangerous || permission.requires_confirmation,
  ).length;

  const openCreateEditor = () => {
    setEditorDirty(false);
    setEditor({ mode: "create" });
  };

  const resetFilters = () => {
    setRoleSearch("");
    setStatusFilter("all");
    setRolePage(1);
  };

  if (!canView) {
    return (
      <AccessDeniedCard title="Роли" message="У вас нет доступа к управлению ролями этой аптеки." />
    );
  }

  if (roles.error) {
    return (
      <div className="space-y-4">
        <PageHeader title="Роли" description="Рабочие роли сотрудников и доступные им функции." />
        <p className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground">
          {describeApiError(roles.error, "Не удалось загрузить список")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Роли"
        description="Рабочие роли сотрудников и доступные им функции."
        meta={
          !roles.isLoading
            ? filteredRoleViews.length === tenantRoles.length
              ? `Всего: ${tenantRoles.length}`
              : `Показано: ${filteredRoleViews.length} из ${tenantRoles.length}`
            : undefined
        }
        actions={
          canCreate ? (
            <Button onClick={openCreateEditor}>
              <PlusIcon />
              Создать роль
            </Button>
          ) : undefined
        }
      />

      <Card role="region" aria-label="Сводка управления ролями" className="overflow-hidden">
        <CardContent className="p-0">
          <dl className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
            <AccessMetric
              icon={<RoleIcon />}
              label="Рабочие роли"
              value={roles.isLoading ? <Skeleton className="h-7 w-10" /> : tenantRoles.length}
            />
            <AccessMetric
              icon={<ActiveIcon />}
              label="Активные"
              value={roles.isLoading ? <Skeleton className="h-7 w-10" /> : activeRoleCount}
              tone="success"
            />
            <AccessMetric
              icon={<FunctionsIcon />}
              label="Доступные функции"
              value={
                !canUseBuilder ? (
                  "—"
                ) : perms.isLoading ? (
                  <Skeleton className="h-7 w-10" />
                ) : (
                  safePermissions.length
                )
              }
            />
            <AccessMetric
              icon={<ShieldIcon />}
              label="С подтверждением"
              value={
                !canUseBuilder ? (
                  "—"
                ) : perms.isLoading ? (
                  <Skeleton className="h-7 w-10" />
                ) : (
                  confirmationPermissionCount
                )
              }
              tone={confirmationPermissionCount > 0 ? "warning" : "neutral"}
            />
          </dl>
        </CardContent>
      </Card>

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
                  autoComplete="off"
                  value={roleSearch}
                  onChange={(event) => {
                    setRoleSearch(event.target.value);
                    setRolePage(1);
                  }}
                  placeholder="Название или описание"
                />
              </div>
            ),
            active: Boolean(roleSearch),
            onClear: () => {
              setRoleSearch("");
              setRolePage(1);
            },
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
                  onChange={(event) => {
                    setStatusFilter(event.target.value as RoleStatusFilter);
                    setRolePage(1);
                  }}
                  className="w-full sm:w-44"
                >
                  <option value="all">Все роли</option>
                  <option value="active">Активные</option>
                  <option value="inactive">Неактивные</option>
                </Select>
              </div>
            ),
            active: statusFilter !== "all",
            onClear: () => {
              setStatusFilter("all");
              setRolePage(1);
            },
            defaultVisible: true,
          },
        ]}
        onResetValues={resetFilters}
      />

      {canUseBuilder && perms.error ? (
        <p
          className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground"
          role="alert"
        >
          {describeApiError(
            perms.error,
            "Не удалось загрузить доступные функции. Изменение ролей временно заблокировано.",
          )}
        </p>
      ) : null}

      <section className="space-y-3" aria-labelledby="tenant-roles-heading">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 id="tenant-roles-heading" className="text-base font-semibold text-foreground">
              Роли аптеки
            </h2>
            <p className="mt-0.5 text-xs text-foreground-muted">
              Пользовательские роли текущей аптеки.
            </p>
          </div>
          {!roles.isLoading ? (
            <Badge tone="neutral" aria-live="polite">
              {roleCountLabel(filteredRoleViews.length)}
            </Badge>
          ) : null}
        </div>

        {roles.isLoading ? (
          <SkeletonRows rows={4} />
        ) : filteredRoleViews.length === 0 ? (
          <TableEmpty
            title={tenantRoles.length > 0 ? "Роли не найдены" : "Роли пока не созданы"}
            action={
              tenantRoles.length === 0 && canCreate ? (
                <Button onClick={openCreateEditor}>
                  <PlusIcon />
                  Создать роль
                </Button>
              ) : undefined
            }
          >
            {tenantRoles.length > 0
              ? "По выбранным фильтрам ролей нет."
              : canCreate
                ? "Создайте первую рабочую роль или начните с шаблона."
                : "Управляемых ролей пока нет."}
          </TableEmpty>
        ) : (
          <>
            <ul className="grid min-w-0 gap-3 lg:grid-cols-2">
              {pagedRoleViews.map((view) => (
                <RoleCard
                  key={view.role.id}
                  view={view}
                  canUpdate={canUpdate}
                  catalogueLoading={canUseBuilder && perms.isLoading}
                  catalogueAvailable={!canUseBuilder || perms.isSuccess}
                  onEdit={() => {
                    setEditorDirty(false);
                    setEditor({ mode: "edit", role: view.role });
                  }}
                />
              ))}
            </ul>
            {filteredRoleViews.length > ROLE_PAGE_SIZE ? (
              <Pagination
                page={visibleRolePage}
                pageSize={ROLE_PAGE_SIZE}
                total={filteredRoleViews.length}
                onPage={setRolePage}
              />
            ) : null}
          </>
        )}
      </section>

      <Modal
        open={editor !== null}
        onClose={requestEditorClose}
        title={editor?.mode === "edit" ? "Изменить роль" : "Создать роль"}
        className="h-[calc(100dvh-1rem)] max-w-[88rem] sm:h-[min(54rem,calc(100dvh-2rem))]"
        bodyClassName="overflow-hidden"
      >
        {editor ? (
          <RoleBuilderLoadBoundary onClose={closeEditor}>
            <Suspense fallback={<SkeletonRows rows={6} />}>
              <RoleBuilderModal
                mode={editor.mode}
                role={editor.mode === "edit" ? editor.role : undefined}
                onClose={closeEditor}
                onCancel={requestEditorClose}
                onDirtyChange={setEditorDirty}
              />
            </Suspense>
          </RoleBuilderLoadBoundary>
        ) : null}
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

function buildRoleView(
  role: Role,
  permissionByCode: ReadonlyMap<string, Permission>,
  hasBuilderAccess: boolean,
  catalogueLoaded: boolean,
): RoleView {
  const groups = new Map<string, number>();
  let visiblePermissionCount = 0;
  let containsUnknownPermission = false;

  if (hasBuilderAccess && catalogueLoaded) {
    for (const code of role.permissions) {
      const permission = permissionByCode.get(code);
      if (!permission) {
        containsUnknownPermission = true;
        continue;
      }
      visiblePermissionCount += 1;
      groups.set(permission.group_code, (groups.get(permission.group_code) ?? 0) + 1);
    }
  }

  return {
    role,
    permissionCount:
      hasBuilderAccess && catalogueLoaded ? visiblePermissionCount : role.permissions.length,
    groups: [...groups].map(([code, count]) => ({ code, count })),
    editBlocked:
      hasBuilderAccess &&
      catalogueLoaded &&
      (hasUnavailableRolePermissions(role) || containsUnknownPermission),
  };
}

function RoleCard({
  view,
  canUpdate,
  catalogueLoading,
  catalogueAvailable,
  onEdit,
}: {
  view: RoleView;
  canUpdate: boolean;
  catalogueLoading: boolean;
  catalogueAvailable: boolean;
  onEdit: () => void;
}): JSX.Element {
  const { role, permissionCount, groups, editBlocked } = view;
  const shownGroups = groups.slice(0, 4);
  const hiddenGroupCount = Math.max(0, groups.length - shownGroups.length);

  return (
    <li className="min-w-0">
      <article className="flex h-full min-h-48 min-w-0 flex-col rounded-lg border border-border bg-surface p-4 sm:p-5">
        <div className="flex min-w-0 items-start gap-3">
          <span
            aria-hidden="true"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"
          >
            <RoleIcon />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h3 className="min-w-0 break-words text-base font-semibold text-foreground">
                {role.name}
              </h3>
              <Badge tone={role.is_active ? "success" : "neutral"}>
                {role.is_active ? "активна" : "неактивна"}
              </Badge>
            </div>
            <p className="mt-1 line-clamp-2 min-h-10 text-sm leading-5 text-foreground-muted">
              {role.description || "Описание роли не задано."}
            </p>
          </div>
        </div>

        <div className="mt-4 flex min-h-7 min-w-0 flex-wrap content-start gap-1.5">
          {catalogueLoading ? (
            <Skeleton className="h-6 w-32" />
          ) : !catalogueAvailable && canUpdate ? (
            <Badge tone="danger">состав недоступен</Badge>
          ) : shownGroups.length > 0 ? (
            <>
              {shownGroups.map(({ code, count }) => (
                <Badge key={code} tone="neutral">
                  {groupLabel(code)} · {count}
                </Badge>
              ))}
              {hiddenGroupCount > 0 ? <Badge tone="neutral">ещё {hiddenGroupCount}</Badge> : null}
            </>
          ) : (
            <Badge tone="neutral">
              {permissionCount === 0
                ? "функции не назначены"
                : permissionCountLabel(permissionCount)}
            </Badge>
          )}
        </div>

        {editBlocked ? (
          <p className="mt-3 flex items-start gap-2 rounded-md bg-warning-subtle px-3 py-2 text-xs leading-5 text-warning-foreground">
            <LockIcon />
            Изменение заблокировано: состав роли выходит за доступный каталог.
          </p>
        ) : null}

        <div className="mt-auto flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <div className="mr-auto flex items-center gap-3 text-xs text-foreground-muted">
            <span>
              <strong className="font-mono text-sm font-semibold text-foreground">
                {catalogueLoading ? "—" : permissionCount}
              </strong>{" "}
              функций
            </span>
            <span>Версия {role.version}</span>
          </div>
          {canUpdate ? (
            <Button
              variant="secondary"
              size="sm"
              disabled={catalogueLoading || !catalogueAvailable || editBlocked}
              title={editBlocked ? ROLE_EDIT_BLOCKED_MESSAGE : undefined}
              onClick={onEdit}
            >
              <EditIcon />
              Изменить
            </Button>
          ) : null}
        </div>
      </article>
    </li>
  );
}

function AccessMetric({
  icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  tone?: "neutral" | "success" | "warning";
}): JSX.Element {
  const toneClass =
    tone === "success"
      ? "bg-success-subtle text-success-foreground"
      : tone === "warning"
        ? "bg-warning-subtle text-warning-foreground"
        : "bg-primary/10 text-primary";

  return (
    <div className="flex min-w-0 items-center gap-3 bg-surface px-4 py-4 sm:px-5">
      <span
        aria-hidden="true"
        className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${toneClass}`}
      >
        {icon}
      </span>
      <div className="min-w-0">
        <dt className="text-xs font-medium text-foreground-muted">{label}</dt>
        <dd className="mt-0.5 min-h-7 font-mono text-xl font-semibold tabular-nums text-foreground">
          {value}
        </dd>
      </div>
    </div>
  );
}

function roleCountLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} роль`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} роли`;
  return `${count} ролей`;
}

function permissionCountLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} функция`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
    return `${count} функции`;
  }
  return `${count} функций`;
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

function RoleIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="8" r="3" />
      <path d="M6.5 19a5.5 5.5 0 0 1 11 0" />
    </svg>
  );
}

function ActiveIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m5 12 4 4L19 6" />
    </svg>
  );
}

function FunctionsIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 6h11M9 12h11M9 18h11" />
      <path d="m4 6 .5.5L6 5M4 12l.5.5L6 11M4 18l.5.5L6 17" />
    </svg>
  );
}

function ShieldIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3 5 6v5c0 4.6 2.9 8.2 7 10 4.1-1.8 7-5.4 7-10V6l-7-3Z" />
      <path d="M12 8v4M12 16h.01" />
    </svg>
  );
}

function EditIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z" />
    </svg>
  );
}

function LockIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className="mt-0.5 shrink-0"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect width="16" height="11" x="4" y="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}
