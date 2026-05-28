import { useMemo } from "react";

import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { describeApiError } from "@/features/foundation/errors";

import { usePermissionsQuery, useRolesQuery } from "./queries";

const levelLabel = (lvl: number) => {
  switch (lvl) {
    case 1:
      return "Уровень 1 — Владелец";
    case 2:
      return "Уровень 2 — Управляющий";
    case 3:
      return "Уровень 3 — Сотрудник";
    case 4:
      return "Уровень 4 — Кассир";
    default:
      return `Уровень ${lvl}`;
  }
};

export function RolesPage(): JSX.Element {
  const roles = useRolesQuery();
  const perms = usePermissionsQuery();

  // Build a code → name map for nicer permission rendering.
  const permName = useMemo(() => {
    const m = new Map<string, string>();
    perms.data?.forEach((p) => m.set(p.code, p.name));
    return m;
  }, [perms.data]);

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

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">Роли</h1>
      <p className="text-sm text-foreground-muted">
        Системные роли защищены. Кастомные роли появятся в Этапе 2.
      </p>
      {roles.isLoading ? (
        <p className="text-sm text-foreground-muted">Загрузка…</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {roles.data?.map((r) => (
            <Card key={r.id}>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>{r.name}</CardTitle>
                  <div className="flex gap-1">
                    {r.is_system && <Badge tone="info">системная</Badge>}
                    {!r.is_active && <Badge tone="neutral">неактивна</Badge>}
                  </div>
                </div>
                <p className="text-xs text-foreground-muted">{levelLabel(r.level)}</p>
              </CardHeader>
              <CardContent className="space-y-2">
                {r.description && <p className="text-sm text-foreground-secondary">{r.description}</p>}
                <div>
                  <p className="text-xs uppercase tracking-wide text-foreground-muted">
                    Права ({r.permissions.length})
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {r.permissions.length === 0 ? (
                      <span className="text-xs italic text-foreground-muted">нет прав</span>
                    ) : (
                      r.permissions.map((code) => (
                        <Badge key={code} tone="neutral" title={code}>
                          {permName.get(code) ?? code}
                        </Badge>
                      ))
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
