import { useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Modal } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/features/foundation/errors";

import { RoleBuilderModal } from "./RoleBuilderModal";
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
  const hasTenant = Boolean(user?.home_tenant_id);
  const userPermissions = user?.permissions ?? [];
  const canCreate = userPermissions.includes("roles.create");
  const canUpdate = userPermissions.includes("roles.update");
  const canAssign = userPermissions.includes("roles.assign");
  const canView =
    hasTenant && user?.is_tenant_owner === true && (canCreate || canUpdate || canAssign);

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
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-foreground">Роли</h1>
        <p className="text-sm text-danger">
          {describeApiError(roles.error, "Не удалось загрузить список")}
        </p>
      </div>
    );
  }

  const tenantRoles = (roles.data ?? []).filter((role) =>
    isManageableRole(role, user?.home_tenant_id),
  );

  const grantableCodes = new Set(permName.keys());

  const card = (r: Role) => {
    const visiblePermissions = r.permissions.filter((code) => grantableCodes.has(code));
    const editBlocked = perms.isSuccess && hasUnavailableRolePermissions(r);

    return (
      <Card key={r.id}>
        <CardHeader>
          <div className="flex items-start justify-between gap-2">
            <CardTitle>{r.name}</CardTitle>
            <div className="flex shrink-0 gap-1">
              {!r.is_active && <Badge tone="neutral">неактивна</Badge>}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {r.description && <p className="text-sm text-foreground-secondary">{r.description}</p>}
          <div>
            <p className="text-xs uppercase tracking-wide text-foreground-muted">
              Доступные функции ({visiblePermissions.length})
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {visiblePermissions.length === 0 ? (
                <span className="text-xs italic text-foreground-muted">нет доступных функций</span>
              ) : (
                visiblePermissions.map((code) => (
                  <Badge key={code} tone="neutral" title={code}>
                    {permName.get(code)}
                  </Badge>
                ))
              )}
            </div>
            {editBlocked && (
              <p className="mt-2 text-xs text-warning-foreground">
                Некоторые функции этой роли недоступны для изменения.
              </p>
            )}
          </div>
          {canUpdate && (
            <div className="flex justify-end">
              <Button
                variant="secondary"
                size="sm"
                disabled={!perms.isSuccess || editBlocked}
                title={editBlocked ? ROLE_EDIT_BLOCKED_MESSAGE : undefined}
                onClick={() => setEditor({ mode: "edit", role: r })}
              >
                Изменить
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Роли</h1>
        {canCreate && <Button onClick={() => setEditor({ mode: "create" })}>+ Создать роль</Button>}
      </div>

      {perms.error && (
        <p className="text-sm text-danger">
          {describeApiError(perms.error, "Не удалось загрузить названия функций")}
        </p>
      )}

      {roles.isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-foreground-secondary">Роли аптеки</h2>
          {tenantRoles.length === 0 ? (
            <p className="text-sm text-foreground-muted">
              {canCreate
                ? "Управляемых ролей пока нет. Создайте первую роль или начните с шаблона."
                : "Управляемых ролей пока нет."}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">{tenantRoles.map(card)}</div>
          )}
        </section>
      )}

      <Modal
        open={editor !== null}
        onClose={() => setEditor(null)}
        title={editor?.mode === "edit" ? "Изменить роль" : "Создать роль"}
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
