import {
  lazy,
  Suspense,
  type ComponentProps,
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import { useAuth } from "@/features/auth/hooks";
import { activeTenantId } from "@/features/auth/tenantContext";
import { useUserPreferencesQuery } from "@/features/settings/queries";
import { acquireBodyScrollLock } from "@/lib/bodyScrollLock";
import { useConnectivity } from "@/lib/connectivityContext";

import { AppHeader } from "./AppHeader";
import { ConnectivityStatusBanner } from "./ConnectivityStatusBanner";
import { PwaUpdateBanner } from "./PwaUpdateBanner";
import { Sidebar } from "./Sidebar";
import { buildNav, findActiveNavItem } from "./nav";
import {
  defaultSidebarPreferences,
  loadSidebarPreferences,
  orderSidebarItems,
  parseSidebarPreferences,
  saveSidebarPreferences,
  SIDEBAR_PREFERENCES_CHANGED_EVENT,
  sidebarStorageKey,
  type SidebarPreferences,
  visibleSidebarItems,
} from "./sidebarPreferences";
import { type AppRoutePath } from "./routeAccess";

let loadedSectionSearch: typeof import("./SectionSearchDialog") | undefined;

function createSectionSearchDialog() {
  return lazy(async () => {
    if (loadedSectionSearch) return loadedSectionSearch;
    // A failed ESM fetch can remain cached until reload; never initiate one offline.
    if (!navigator.onLine) return { default: SectionSearchUnavailable };
    return import("./SectionSearchDialog")
      .then((module) => {
        loadedSectionSearch = module;
        return module;
      })
      .catch(() => ({ default: SectionSearchUnavailable }));
  });
}

function SectionSearchUnavailable({
  open,
  onClose,
}: ComponentProps<(typeof import("./SectionSearchDialog"))["default"]>): JSX.Element {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { status } = useConnectivity();
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const previousFocus = document.activeElement;
    dialog.showModal();
    const releaseScrollLock = acquireBodyScrollLock();
    return () => {
      dialog.close();
      releaseScrollLock();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, [open]);
  return (
    <dialog
      ref={dialogRef}
      aria-label="Перейти в раздел"
      data-aurum-modal
      className="app-section-search-unavailable"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 className="app-search-error-title">Перейти в раздел</h2>
      <p role="alert" className="app-header-detail">
        Поиск не загрузился. Используйте боковое меню или обновите страницу при наличии сети.
      </p>
      <Button className="mt-4" variant="secondary" onClick={onClose}>
        Закрыть
      </Button>
      <Button
        className="ml-2 mt-4"
        disabled={status === "offline"}
        onClick={() => window.location.reload()}
      >
        Обновить страницу
      </Button>
    </dialog>
  );
}
const SupportAccessBanner = lazy(async () => {
  const module = await import("@/features/supportAccess/SupportAccessBanner");
  return { default: module.SupportAccessBanner };
});

const TenantAccessBanner = lazy(async () => {
  const module = await import("@/features/tenantAccess/TenantAccessBanner");
  return { default: module.TenantAccessBanner };
});

const AccountSecuritySurface = lazy(() => import("@/features/auth/AccountSecuritySurface"));

const SidebarSettingsModal = lazy(async () => {
  const module = await import("./SidebarSettingsModal");
  return { default: module.SidebarSettingsModal };
});

const MobileNavigationPanel = lazy(() => import("./MobileNavigationPanel"));

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const { user, logout, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const preferencesQuery = useUserPreferencesQuery();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [SectionSearchDialog, setSectionSearchDialog] = useState(createSectionSearchDialog);
  const closeSearch = () => {
    setSearchOpen(false);
    setSectionSearchDialog(createSectionSearchDialog());
  };
  const [sidebarSettingsOpen, setSidebarSettingsOpen] = useState(false);
  const navigationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const isSupport = Boolean(user?.is_developer || user?.is_administrator);
  const tenantId = activeTenantId(user);
  const hasTenant = Boolean(tenantId);
  const isTenantOwner = Boolean(user?.is_tenant_owner);
  const perms = user?.permissions;
  const items = useMemo(
    () =>
      buildNav(
        isSupport,
        hasTenant,
        isTenantOwner,
        perms ?? [],
        user?.is_developer === true,
        user?.support_access !== null && user?.support_access !== undefined,
        user?.platform_capabilities ?? [],
      ),
    [
      hasTenant,
      isSupport,
      isTenantOwner,
      perms,
      user?.is_developer,
      user?.platform_capabilities,
      user?.support_access,
    ],
  );
  const sidebarScope = `${user?.id ?? "anonymous"}:${tenantId ?? "global"}`;
  const [sidebarPreferences, setSidebarPreferences] = useState<SidebarPreferences>(() =>
    loadSidebarPreferences(sidebarScope),
  );
  const startRouteHandledScopeRef = useRef<string | null>(null);
  const [wideDesktop, setWideDesktop] = useState(
    () => window.matchMedia("(min-width: 80rem)").matches,
  );
  const orderedItems = useMemo(
    () => orderSidebarItems(items, sidebarPreferences),
    [items, sidebarPreferences],
  );
  const sidebarItems = useMemo(
    () => visibleSidebarItems(items, sidebarPreferences),
    [items, sidebarPreferences],
  );
  const sidebarExpanded =
    sidebarPreferences.desktopMode === "expanded" ||
    (sidebarPreferences.desktopMode === "auto" && wideDesktop);
  const shellStyle = {
    "--app-sidebar-width": sidebarExpanded ? "15rem" : "4.5rem",
  } as CSSProperties;
  const activeItem = findActiveNavItem(items, pathname);
  const pageTitle =
    pathname === "/admin/sync"
      ? "Синхронизация"
      : pathname === "/billing"
        ? "Расчёты с Aurum"
        : pathname === "/profile"
          ? "Мой профиль"
          : (activeItem?.pageTitle ?? activeItem?.label ?? "Aurum Pharma");
  const pageOwnsDesktopTitle =
    pathname === "/" ||
    pathname === "/profile" ||
    pathname === "/my-work" ||
    pathname === "/staff-pay" ||
    pathname === "/catalog" ||
    pathname === "/sales" ||
    pathname === "/suppliers" ||
    pathname === "/incoming" ||
    pathname === "/batches" ||
    pathname === "/onboarding" ||
    pathname === "/reports";
  const workspaceLabel =
    user?.support_access?.tenant_name ?? (hasTenant ? "Рабочая область аптеки" : "Платформа Aurum");

  const name = user?.full_name?.trim() || "Мой аккаунт";
  const caption = user?.is_developer
    ? "Разработчик"
    : user?.is_administrator
      ? "Администратор"
      : isTenantOwner
        ? "Владелец аптеки"
        : "Сотрудник аптеки";
  const quickFavorites = sidebarItems.filter((item) =>
    sidebarPreferences.favoriteRoutes.includes(item.to),
  );
  const defaultQuickRoutes =
    isSupport && !hasTenant ? ["/", "/admin"] : ["/", "/pos", "/catalog", "/incoming", "/reports"];
  const quickItems =
    quickFavorites.length > 0
      ? quickFavorites
      : defaultQuickRoutes.flatMap((to) => sidebarItems.filter((item) => item.to === to));
  const serverSidebarPreferences = useMemo(
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

  const updateSidebarPreferences = useCallback(
    (next: SidebarPreferences) => {
      const parsed = parseSidebarPreferences(next);
      setSidebarPreferences(parsed);
      saveSidebarPreferences(sidebarScope, parsed);
    },
    [sidebarScope],
  );

  const openNavigation = (trigger: HTMLButtonElement) => {
    navigationTriggerRef.current = trigger;
    setNavigationOpen(true);
  };

  const openSidebarSettings = () => {
    setNavigationOpen(false);
    setSidebarSettingsOpen(true);
  };

  useEffect(() => {
    const openFromSettingsHub = () => {
      setNavigationOpen(false);
      setSidebarSettingsOpen(true);
    };
    window.addEventListener("aurum:open-sidebar-settings", openFromSettingsHub);
    return () => {
      window.removeEventListener("aurum:open-sidebar-settings", openFromSettingsHub);
    };
  }, []);

  useEffect(() => {
    setSidebarPreferences(loadSidebarPreferences(sidebarScope));

    const key = sidebarStorageKey(sidebarScope);
    const onStorage = (event: StorageEvent) => {
      if (event.key !== key) return;
      try {
        setSidebarPreferences(
          event.newValue === null
            ? loadSidebarPreferences(sidebarScope)
            : parseSidebarPreferences(JSON.parse(event.newValue)),
        );
      } catch {
        setSidebarPreferences(loadSidebarPreferences(sidebarScope));
      }
    };
    const onSameTabChange = (event: Event) => {
      const detail = (event as CustomEvent<{ scope?: string }>).detail;
      if (detail?.scope === sidebarScope) {
        setSidebarPreferences(loadSidebarPreferences(sidebarScope));
      }
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, onSameTabChange);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(SIDEBAR_PREFERENCES_CHANGED_EVENT, onSameTabChange);
    };
  }, [sidebarScope]);

  useEffect(() => {
    if (!serverSidebarPreferences) return;
    const local = loadSidebarPreferences(sidebarScope);
    const serverIsDefault = sameSidebarPreferences(
      serverSidebarPreferences,
      defaultSidebarPreferences(),
    );
    const localIsDefault = sameSidebarPreferences(local, defaultSidebarPreferences());
    const alreadySynced = hasServerSidebarMarker(sidebarScope);

    // Preserve one legacy local customization, then treat the account copy as
    // authoritative on every subsequent device and session.
    if (!alreadySynced && serverIsDefault && !localIsDefault) return;
    markServerSidebarSynced(sidebarScope);
    if (!sameSidebarPreferences(local, serverSidebarPreferences)) {
      saveSidebarPreferences(sidebarScope, serverSidebarPreferences);
    } else {
      setSidebarPreferences(serverSidebarPreferences);
    }
  }, [serverSidebarPreferences, sidebarScope]);

  useEffect(() => {
    if (!preferencesQuery.data || startRouteHandledScopeRef.current === sidebarScope) return;
    startRouteHandledScopeRef.current = sidebarScope;

    const marker = startRouteSessionKey(sidebarScope);
    if (hasSessionMarker(marker)) return;
    setSessionMarker(marker);
    if (pathname !== "/") return;

    const requested = preferencesQuery.data.workspace.start_route;
    const target = items.find((item) => item.to === requested)?.to as AppRoutePath | undefined;
    if (target && target !== "/") void navigate({ to: target, replace: true });
  }, [items, navigate, pathname, preferencesQuery.data, sidebarScope]);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 64rem)");
    const wide = window.matchMedia("(min-width: 80rem)");
    const closeDrawerOnDesktop = (event: MediaQueryListEvent) => {
      if (event.matches) setNavigationOpen(false);
    };
    const updateWideDesktop = (event: MediaQueryListEvent) => setWideDesktop(event.matches);
    desktop.addEventListener("change", closeDrawerOnDesktop);
    wide.addEventListener("change", updateWideDesktop);
    return () => {
      desktop.removeEventListener("change", closeDrawerOnDesktop);
      wide.removeEventListener("change", updateWideDesktop);
    };
  }, []);

  useEffect(() => {
    if (!navigationOpen) return undefined;

    const previousActiveElement =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const navigationTrigger = navigationTriggerRef.current;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setNavigationOpen(false);
      }
    };
    const releaseScrollLock = acquireBodyScrollLock();
    document.addEventListener("keydown", onKeyDown);
    return () => {
      releaseScrollLock();
      document.removeEventListener("keydown", onKeyDown);
      if (previousActiveElement && document.contains(previousActiveElement)) {
        previousActiveElement.focus();
      } else {
        navigationTrigger?.focus();
      }
    };
  }, [navigationOpen]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "k") return;
      if (document.querySelector("[data-aurum-modal]") || navigationOpen) return;
      event.preventDefault();
      setNavigationOpen(false);
      setSearchOpen(true);
    };
    document.addEventListener("keydown", onShortcut);
    return () => document.removeEventListener("keydown", onShortcut);
  }, [navigationOpen]);

  return (
    <div data-ui-version="2026" className="min-h-screen bg-background" style={shellStyle}>
      <a href="#main-content" className="app-shell-skip-link">
        Перейти к содержимому
      </a>
      {navigationOpen && (
        <div className="fixed inset-0 z-overlay" role="presentation">
          <button
            type="button"
            aria-label="Закрыть меню через фон"
            className="absolute inset-0 bg-overlay"
            onClick={() => setNavigationOpen(false)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Меню приложения"
            className="relative z-modal h-full w-[min(17rem,calc(100vw-1rem))]"
          >
            <Suspense
              fallback={
                <Button autoFocus onClick={() => setNavigationOpen(false)}>
                  Закрыть меню
                </Button>
              }
            >
              <MobileNavigationPanel
                items={sidebarItems}
                favoriteRoutes={sidebarPreferences.favoriteRoutes}
                onClose={() => setNavigationOpen(false)}
                onOpenSettings={openSidebarSettings}
              />
            </Suspense>
          </div>
        </div>
      )}

      <AppHeader
        workspaceLabel={workspaceLabel}
        pageTitle={pageTitle}
        showPageTitle={!pageOwnsDesktopTitle}
        name={name}
        caption={caption}
        sidebarExpanded={sidebarExpanded}
        quickItems={quickItems}
        hasFavorites={quickFavorites.length > 0}
        canOpenMyWork={items.some((item) => item.to === "/my-work")}
        pathname={pathname}
        navigationOpen={navigationOpen}
        onOpenNavigation={openNavigation}
        onOpenSearch={() => setSearchOpen(true)}
        onOpenSettings={openSidebarSettings}
        onLogout={() => void logout()}
      />
      {searchOpen ? (
        <Suspense fallback={null}>
          <SectionSearchDialog
            open
            items={orderedItems}
            onClose={closeSearch}
            onSelect={(to) => {
              if (!items.some((item) => item.to === to)) return;
              closeSearch();
              void navigate({ to });
            }}
          />
        </Suspense>
      ) : null}

      <div className="lg:pl-[var(--app-sidebar-width)]">
        {user?.support_access ? (
          <Suspense fallback={<p role="status">Загружаем данные доступа поддержки…</p>}>
            <SupportAccessBanner />
          </Suspense>
        ) : null}
        {isAuthenticated && hasTenant ? (
          <Suspense fallback={null}>
            <TenantAccessBanner />
          </Suspense>
        ) : null}
        <ConnectivityStatusBanner />
        <PwaUpdateBanner />
      </div>

      <div className="min-w-0 lg:grid lg:grid-cols-[var(--app-sidebar-width)_minmax(0,1fr)]">
        <div className="hidden lg:block">
          <Sidebar
            items={sidebarItems}
            expanded={sidebarExpanded}
            favoriteRoutes={sidebarPreferences.favoriteRoutes}
            onToggleExpanded={() =>
              updateSidebarPreferences({
                ...sidebarPreferences,
                desktopMode: sidebarExpanded ? "compact" : "expanded",
              })
            }
            onOpenSettings={openSidebarSettings}
          />
        </div>
        <main id="main-content" tabIndex={-1} className="app-shell-main">
          <Suspense fallback={null}>
            <AccountSecuritySurface
              showPrompt={
                pathname !== "/security" && pathname !== "/settings" && pathname !== "/profile"
              }
            />
          </Suspense>
          {children}
        </main>
      </div>

      {sidebarSettingsOpen ? (
        <Suspense fallback={null}>
          <SidebarSettingsModal
            open
            items={orderedItems}
            preferences={sidebarPreferences}
            activeRoute={activeItem?.to}
            onChange={updateSidebarPreferences}
            onClose={() => setSidebarSettingsOpen(false)}
          />
        </Suspense>
      ) : null}
    </div>
  );
}

const SIDEBAR_SERVER_MARKER_PREFIX = "aurum:sidebar-server:v1";
const START_ROUTE_SESSION_PREFIX = "aurum:start-route:v1";

function sameSidebarPreferences(left: SidebarPreferences, right: SidebarPreferences): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sidebarServerMarkerKey(scope: string): string {
  return `${SIDEBAR_SERVER_MARKER_PREFIX}:${encodeURIComponent(scope)}`;
}

function hasServerSidebarMarker(scope: string): boolean {
  try {
    return window.localStorage.getItem(sidebarServerMarkerKey(scope)) === "1";
  } catch {
    return false;
  }
}

function markServerSidebarSynced(scope: string): void {
  try {
    window.localStorage.setItem(sidebarServerMarkerKey(scope), "1");
  } catch {
    // The synchronized in-memory state still works when storage is blocked.
  }
}

function startRouteSessionKey(scope: string): string {
  return `${START_ROUTE_SESSION_PREFIX}:${encodeURIComponent(scope)}`;
}

function hasSessionMarker(key: string): boolean {
  try {
    return window.sessionStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function setSessionMarker(key: string): void {
  try {
    window.sessionStorage.setItem(key, "1");
  } catch {
    // The component ref prevents repeated redirects for this mounted session.
  }
}
