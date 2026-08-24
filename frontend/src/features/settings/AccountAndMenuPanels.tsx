import { useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";

import { Badge, Button, Select } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { buildNav } from "@/components/layout/nav";
import {
  defaultSidebarPreferences,
  loadSidebarPreferences,
  parseSidebarPreferences,
  saveSidebarPreferences,
  SIDEBAR_PREFERENCES_CHANGED_EVENT,
  type SidebarPreferences,
} from "@/components/layout/sidebarPreferences";

import { useUpdateUserPreferences, useUserPreferencesQuery } from "./queries";
import { SettingRow, SettingsNotice, SettingsSectionHeader } from "./SettingsPrimitives";

export function AccountSettingsPanel(): JSX.Element {
  const { user } = useAuth();
  const accessLabel = user?.is_developer
    ? "Разработчик Aurum"
    : user?.is_administrator
      ? "Администратор Aurum"
      : user?.is_tenant_owner
        ? "Владелец аптеки"
        : "Сотрудник аптеки";

  return (
    <div className="space-y-4">
      <SettingsSectionHeader
        title="Мой аккаунт"
        description="Данные текущей учётной записи и быстрый доступ к личной безопасности."
      />

      <div>
        <SettingRow title="Имя">
          <div className="text-sm font-medium text-foreground">
            {user?.full_name || "Не указано"}
          </div>
        </SettingRow>
        <SettingRow title="Email">
          <div className="break-all text-sm text-foreground-secondary">{user?.email ?? "—"}</div>
        </SettingRow>
        <SettingRow title="Уровень доступа">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Badge tone={user?.is_tenant_owner ? "success" : "neutral"}>{accessLabel}</Badge>
            <span className="text-xs text-foreground-muted">Уровень {user?.level ?? "—"}</span>
          </div>
        </SettingRow>
        <SettingRow title="Состояние аккаунта">
          <div>
            <Badge tone={user?.status === "active" ? "success" : "warning"}>
              {user?.status === "active" ? "Активен" : "Требует внимания"}
            </Badge>
          </div>
        </SettingRow>
      </div>

      <div className="flex flex-wrap gap-3 border-t border-border pt-4">
        <Link
          to="/security"
          className="inline-flex h-[var(--control-height-md)] items-center rounded-md border border-border bg-surface px-4 text-sm font-medium text-foreground hover:bg-foreground/[0.04]"
        >
          Активные сеансы
        </Link>
        <Link
          to="/notifications"
          className="inline-flex h-[var(--control-height-md)] items-center rounded-md border border-border bg-surface px-4 text-sm font-medium text-foreground hover:bg-foreground/[0.04]"
        >
          Центр уведомлений
        </Link>
      </div>
    </div>
  );
}

export function MenuSettingsPanel(): JSX.Element {
  const { user } = useAuth();
  const tenantId = activeTenantId(user);
  const scope = `${user?.id ?? "anonymous"}:${tenantId ?? "global"}`;
  const preferencesQuery = useUserPreferencesQuery();
  const update = useUpdateUserPreferences();
  const [sidebar, setSidebar] = useState<SidebarPreferences>(() => loadSidebarPreferences(scope));
  const [startRoute, setStartRoute] = useState("/");
  const navigationItems = useMemo(
    () =>
      buildNav(
        Boolean(user?.is_developer || user?.is_administrator),
        Boolean(tenantId),
        Boolean(user?.is_tenant_owner),
        user?.permissions ?? [],
        user?.is_developer === true,
        user?.support_access !== null && user?.support_access !== undefined,
        user?.platform_capabilities ?? [],
      ),
    [tenantId, user],
  );
  const serverSidebar = useMemo(
    () =>
      preferencesQuery.data
        ? parseSidebarPreferences({
            desktopMode: preferencesQuery.data.workspace.desktop_mode,
            hiddenRoutes: preferencesQuery.data.workspace.hidden_routes,
            favoriteRoutes: preferencesQuery.data.workspace.favorite_routes,
            routeOrder: preferencesQuery.data.workspace.route_order,
          })
        : null,
    [preferencesQuery.data],
  );

  useEffect(() => {
    const local = loadSidebarPreferences(scope);
    const next = serverSidebar && !isDefaultSidebar(serverSidebar) ? serverSidebar : local;
    setSidebar(next);
    if (next === serverSidebar) saveSidebarPreferences(scope, next);

    const syncFromStorage = (event: Event) => {
      const detail = (event as CustomEvent<{ scope?: string }>).detail;
      if (detail?.scope === scope) setSidebar(loadSidebarPreferences(scope));
    };
    window.addEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, syncFromStorage);
    return () => window.removeEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, syncFromStorage);
  }, [scope, serverSidebar]);

  useEffect(() => {
    if (!preferencesQuery.data) return;
    const savedRoute = preferencesQuery.data.workspace.start_route;
    setStartRoute(
      navigationItems.some((item) => item.to === savedRoute)
        ? savedRoute
        : (navigationItems[0]?.to ?? "/settings"),
    );
  }, [navigationItems, preferencesQuery.data]);

  const dirty =
    serverSidebar === null ||
    !sameSidebar(sidebar, serverSidebar) ||
    startRoute !== preferencesQuery.data?.workspace.start_route;
  const modeLabel =
    sidebar.desktopMode === "compact"
      ? "Компактное"
      : sidebar.desktopMode === "expanded"
        ? "Расширенное"
        : "Автоматическое";

  const saveToAccount = () => {
    if (!preferencesQuery.data || update.isPending) return;
    update.mutate({
      expected_version: preferencesQuery.data.version,
      workspace: {
        desktop_mode: sidebar.desktopMode,
        hidden_routes: sidebar.hiddenRoutes,
        favorite_routes: sidebar.favoriteRoutes,
        route_order: sidebar.routeOrder,
        start_route: startRoute,
      },
    });
  };

  return (
    <div className="space-y-4">
      <SettingsSectionHeader
        title="Меню и старт"
        description="Порядок, избранные разделы и ширина боковой панели для вашего аккаунта."
      />

      {update.error ? (
        <SettingsNotice tone="warning">
          Меню сохранено в этом браузере, но пока не синхронизировано с аккаунтом.
        </SettingsNotice>
      ) : null}

      <div>
        <SettingRow title="Вид меню">
          <span className="text-sm font-medium text-foreground">{modeLabel}</span>
        </SettingRow>
        <SettingRow title="Избранные разделы">
          <span className="text-sm text-foreground-secondary">{sidebar.favoriteRoutes.length}</span>
        </SettingRow>
        <SettingRow title="Скрытые разделы">
          <span className="text-sm text-foreground-secondary">{sidebar.hiddenRoutes.length}</span>
        </SettingRow>
        <SettingRow
          title="Стартовый раздел"
          description="Откроется первым после входа в новом сеансе."
        >
          <Select
            aria-label="Стартовый раздел"
            value={startRoute}
            className="w-full sm:w-64"
            onChange={(event) => setStartRoute(event.target.value)}
          >
            {navigationItems.map((item) => (
              <option key={item.to} value={item.to}>
                {item.pageTitle ?? item.label}
              </option>
            ))}
          </Select>
        </SettingRow>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
        <Button
          type="button"
          variant="secondary"
          onClick={() => window.dispatchEvent(new Event("aurum:open-sidebar-settings"))}
        >
          Настроить меню
        </Button>
        <div className="flex items-center gap-3">
          {!dirty && !update.isPending ? (
            <span className="text-xs text-success-foreground">Синхронизировано</span>
          ) : null}
          <Button
            type="button"
            disabled={!dirty || preferencesQuery.data === undefined}
            isLoading={update.isPending}
            onClick={saveToAccount}
          >
            Сохранить в аккаунт
          </Button>
        </div>
      </div>
    </div>
  );
}

function isDefaultSidebar(value: SidebarPreferences): boolean {
  return sameSidebar(value, defaultSidebarPreferences());
}

function sameSidebar(left: SidebarPreferences, right: SidebarPreferences): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
