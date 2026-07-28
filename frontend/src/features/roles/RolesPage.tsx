import { useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
  Modal,
  PageHeader,
  SkeletonRows,
  TableEmpty,
} from "@/components/ui";
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

export function RolesPage(): JSX.Element {
  const { user } = useAuth();
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

  const grantableCodes = new Set(permName.keys());

  const card = (r: Role) => {
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
      <Card key={r.id} className="flex h-full flex-col">
        <CardHeader className="border-b-0 pb-2">
          <div className="flex items-start justify-between gap-2">
            <CardTitle>{r.name}</CardTitle>
            <div className="flex shrink-0 gap-1">
              {!r.is_active && <Badge tone="neutral">неактивна</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-4 pt-1">
          <p className="min-h-10 text-sm leading-5 text-foreground-secondary">
            {r.description || "Описание роли не задано."}
          </p>
          <div className="border-t border-border pt-3">
            <div className="flex items-center justify-between gap-3 text-xs text-foreground-muted">
              <span>Доступные функции</span>
              <span className="font-mono font-semibold text-foreground">
                {visiblePermissions.length}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {shownGroups.length === 0 ? (
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
              <p className="mt-3 text-xs leading-5 text-warning-foreground">
                Некоторые функции этой роли недоступны для изменения.
              </p>
            )}
          </div>
        </CardContent>
        {canUpdate && (
          <CardFooter className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              disabled={!perms.isSuccess || editBlocked}
              title={editBlocked ? ROLE_EDIT_BLOCKED_MESSAGE : undefined}
              onClick={() => setEditor({ mode: "edit", role: r })}
            >
              Изменить
            </Button>
          </CardFooter>
        )}
      </Card>
    );
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Роли"
        meta={!roles.isLoading ? `${tenantRoles.length}` : undefined}
        actions={
          canCreate ? (
            <Button onClick={() => setEditor({ mode: "create" })}>+ Создать роль</Button>
          ) : undefined
        }
      />

      {perms.error && (
        <p className="rounded-lg border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger-foreground">
          {describeApiError(perms.error, "Не удалось загрузить названия функций")}
        </p>
      )}

      {roles.isLoading ? (
        <SkeletonRows rows={4} />
      ) : (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground-secondary">Роли аптеки</h2>
          {tenantRoles.length === 0 ? (
            <TableEmpty title="Роли пока не созданы">
              {canCreate
                ? "Создайте первую роль или начните с готового шаблона."
                : "Управляемых ролей пока нет."}
            </TableEmpty>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {tenantRoles.map(card)}
            </div>
          )}
        </section>
      )}

      <Modal
        open={editor !== null}
        onClose={() => setEditor(null)}
        title={editor?.mode === "edit" ? "Изменить роль" : "Создать роль"}
        className="max-w-5xl"
      >
        {editor && (
          <RoleBuilderModal
            mode={editor.mode}
            role={editor.mode === "edit" ? editor.role : undefined}
            onClose={() => setEditor(null)}
          />
        )}
      </Modal>
    </div>
  );
}
