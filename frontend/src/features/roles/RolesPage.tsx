import { useMemo, useState } from "react";

import { AccessDeniedCard } from "@/components/AccessDeniedCard";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Modal } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { describeApiError } from "@/features/foundation/errors";

import { RoleBuilderModal } from "./RoleBuilderModal";
import { levelLabel } from "./labels";
import { usePermissionsQuery, useRolesQuery } from "./queries";
import { type Role } from "./types";

type Editor = { mode: "create" } | { mode: "edit"; role: Role } | null;

export function RolesPage(): JSX.Element {
  const { user } = useAuth();
  const hasTenant = Boolean(user?.home_tenant_id);
  const isSupport = Boolean(user?.is_developer || user?.is_administrator);
  // Same gate as «Пользователи» — team management lives behind users.view.
  const canView = isSupport || (user?.permissions ?? []).includes("users.view");
  // Building/editing roles needs roles.create on top of merely viewing them.
  const canManage = isSupport || (user?.permissions ?? []).includes("roles.create");

  const roles = useRolesQuery(canView);
  const perms = usePermissionsQuery(canView);
  const [editor, setEditor] = useState<Editor>(null);

  const permName = useMemo(() => {
    const m = new Map<string, string>();
    perms.data?.forEach((p) => m.set(p.code, p.name));
    return m;
  }, [perms.data]);

  if (hasTenant && !canView) {
    return (
      <AccessDeniedCard
        title="Роли"
        message="Управление ролями доступно владельцу и администратору."
      />
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

  const all = roles.data ?? [];
  const systemRoles = all.filter((r) => r.is_system);
  const tenantRoles = all.filter((r) => !r.is_system);

  const card = (r: Role) => (
    <Card key={r.id}>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle>{r.name}</CardTitle>
          <div className="flex shrink-0 gap-1">
            {r.is_system && <Badge tone="info">защищена</Badge>}
            {!r.is_active && <Badge tone="neutral">неактивна</Badge>}
          </div>
        </div>
        <p className="text-xs text-foreground-muted">{levelLabel(r.level)}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        {r.description && <p className="text-sm text-foreground-secondary">{r.description}</p>}
        <div>
          <p className="text-xs uppercase tracking-wide text-foreground-muted">
            Функции ({r.permissions.length})
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {r.permissions.length === 0 ? (
              <span className="text-xs italic text-foreground-muted">нет функций</span>
            ) : (
              r.permissions.map((code) => (
                <Badge key={code} tone="neutral" title={code}>
                  {permName.get(code) ?? code}
                </Badge>
              ))
            )}
          </div>
        </div>
        {!r.is_system && canManage && (
          <div className="flex justify-end">
            <Button variant="secondary" size="sm" onClick={() => setEditor({ mode: "edit", role: r })}>
              Изменить
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-foreground">Роли</h1>
        {canManage && <Button onClick={() => setEditor({ mode: "create" })}>+ Создать роль</Button>}
      </div>

      {roles.isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : (
        <>
          <section className="space-y-3">
            <h2 className="text-sm font-medium text-foreground-secondary">Роли аптеки</h2>
            {tenantRoles.length === 0 ? (
              <p className="text-sm text-foreground-muted">
                Своих ролей пока нет. Нажмите «Создать роль», чтобы собрать первую — можно начать с
                готового шаблона.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">{tenantRoles.map(card)}</div>
            )}
          </section>

          <section className="space-y-3">
            <h2 className="text-sm font-medium text-foreground-secondary">
              Системные роли (защищены)
            </h2>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">{systemRoles.map(card)}</div>
          </section>
        </>
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
