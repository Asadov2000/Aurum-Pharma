import {
  lazy,
  Suspense,
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouterState } from "@tanstack/react-router";

import { Button } from "@/components/ui";
import { AppearanceMenu } from "@/components/AppearanceMenu";
import { useAuth } from "@/features/auth/hooks";
import { useMfaStepUpRequested } from "@/features/auth/stepUpCoordinator";
import { activeTenantId } from "@/features/auth/tenantContext";
import { SupportAccessBanner } from "@/features/supportAccess/SupportAccessBanner";
import { cn } from "@/lib/utils";

import { BrandMark } from "./BrandMark";
import { ConnectivityIndicator } from "./ConnectivityIndicator";
import { OfflineStatusBanner } from "./OfflineStatusBanner";
import { PwaInstallButton } from "./PwaInstallButton";
import { PwaUpdateBanner } from "./PwaUpdateBanner";
import { RuntimeSurfaceBadge } from "./RuntimeSurfaceBadge";
import { ServerStatusBanner } from "./ServerStatusBanner";
import { Sidebar } from "./Sidebar";
import { buildNav } from "./nav";
import {
  loadSidebarPreferences,
  orderSidebarItems,
  parseSidebarPreferences,
  saveSidebarPreferences,
  sidebarStorageKey,
  type SidebarPreferences,
  visibleSidebarItems,
} from "./sidebarPreferences";

const MfaStepUpDialog = lazy(async () => {
  const module = await import("@/features/auth/MfaStepUpDialog");
  return { default: module.MfaStepUpDialog };
});

const SidebarSettingsModal = lazy(async () => {
  const module = await import("./SidebarSettingsModal");
  return { default: module.SidebarSettingsModal };
});

const MfaStepUpDialog = lazy(async () => {
  const module = await import("@/features/auth/MfaStepUpDialog");
  return { default: module.MfaStepUpDialog };
});

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const { user, logout } = useAuth();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const mfaStepUpRequested = useMfaStepUpRequested();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [sidebarSettingsOpen, setSidebarSettingsOpen] = useState(false);
  const navigationTriggerRef = useRef<HTMLButtonElement | null>(null);
  const navigationDrawerRef = useRef<HTMLDivElement>(null);
  const navigationCloseButtonRef = useRef<HTMLButtonElement>(null);
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
      ),
    [hasTenant, isSupport, isTenantOwner, perms, user?.is_developer, user?.support_access],
  );
  const sidebarScope = `${user?.id ?? "anonymous"}:${tenantId ?? "global"}`;
  const [sidebarPreferences, setSidebarPreferences] = useState<SidebarPreferences>(() =>
    loadSidebarPreferences(sidebarScope),
  );
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
  const activeItem = items.find(
    (item) => pathname === item.to || (item.to !== "/" && pathname.startsWith(`${item.to}/`)),
  );
  const pageTitle = activeItem?.pageTitle ?? activeItem?.label ?? "Aurum Pharma";

  // Clean identity: a recognizable name + a quiet caption. We have no role name
  // in the payload, so the caption is the support role (dev/admin) when set, or
  // the email for tenant users (what they recognize) — never the English "Account".
  const name = user?.full_name?.trim() || user?.email || "—";
  const caption = user?.is_developer
    ? "Разработчик"
    : user?.is_administrator
      ? "Администратор"
      : user?.full_name?.trim()
        ? (user.email ?? null)
        : null;
  const initial = name.charAt(0).toUpperCase();

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
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [sidebarScope]);

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
    const focusableSelector = [
      "a[href]",
      "button:not([disabled])",
      "textarea:not([disabled])",
      "input:not([disabled])",
      "select:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",");

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setNavigationOpen(false);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }

      const drawer = navigationDrawerRef.current;
      if (!drawer) {
        return;
      }
      const focusable = Array.from(drawer.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (el) => el.offsetParent !== null && el.tabIndex >= 0,
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        return;
      }

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const previousOverflow = document.body.style.overflow;
    const focusFrame = window.requestAnimationFrame(() => {
      navigationCloseButtonRef.current?.focus();
    });
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      if (previousActiveElement && document.contains(previousActiveElement)) {
        previousActiveElement.focus();
      } else {
        navigationTrigger?.focus();
      }
    };
  }, [navigationOpen]);

  return (
    <div className="min-h-screen bg-background" style={shellStyle}>
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-toast focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-primary-foreground"
      >
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
            ref={navigationDrawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Меню приложения"
            className="relative z-modal h-full w-[min(17rem,calc(100vw-1rem))]"
          >
            <Sidebar
              items={sidebarItems}
              mode="drawer"
              favoriteRoutes={sidebarPreferences.favoriteRoutes}
              onNavigate={() => setNavigationOpen(false)}
              onOpenSettings={openSidebarSettings}
              closeButton={
                <Button
                  ref={navigationCloseButtonRef}
                  variant="ghost"
                  size="sm"
                  className="h-9 w-9 px-0"
                  aria-label="Закрыть меню"
                  onClick={() => setNavigationOpen(false)}
                >
                  <CloseIcon />
                </Button>
              }
            />
          </div>
        </div>
      )}

      <header
        data-testid="app-shell-header"
        className="sticky top-0 z-sticky border-b border-border bg-surface"
      >
        <div className="flex min-h-[var(--app-header-height)] items-stretch">
          <div className="hidden w-[var(--app-sidebar-width)] shrink-0 items-center border-r border-border px-4 lg:flex">
            <BrandMark />
            <span
              className={cn(
                "ml-3 truncate font-display text-lg font-semibold text-foreground",
                !sidebarExpanded && "sr-only",
              )}
            >
              Aurum Pharma
            </span>
          </div>

          <div className="flex min-w-0 flex-1 items-center justify-between gap-3 px-3 sm:px-4 xl:px-5">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <Button
                variant="secondary"
                size="sm"
                className="h-9 w-9 shrink-0 px-0 lg:hidden"
                aria-label="Открыть меню"
                aria-expanded={navigationOpen}
                onClick={(event) => openNavigation(event.currentTarget)}
              >
                <MenuIcon />
              </Button>
              <span className="truncate font-display text-base font-semibold text-foreground sm:text-lg lg:hidden">
                Aurum Pharma
              </span>
              <h1 className="hidden truncate font-display text-xl font-semibold leading-none text-foreground lg:block">
                {pageTitle}
              </h1>
            </div>

            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              <ConnectivityIndicator />
              <PwaInstallButton />
              <RuntimeSurfaceBadge />
              <div className="hidden min-w-0 items-center gap-2 border-l border-border pl-3 2xl:flex">
                <span
                  aria-hidden="true"
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary/10 font-display text-xs font-semibold text-primary"
                >
                  {initial}
                </span>
                <span className="min-w-0 max-w-44 leading-tight">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {name}
                  </span>
                  {caption && (
                    <span className="block truncate text-xs text-foreground-muted">{caption}</span>
                  )}
                </span>
              </div>
              <AppearanceMenu />
              <Button
                variant="secondary"
                size="sm"
                className="h-9 w-9 px-0 xl:w-auto xl:px-3"
                onClick={() => void logout()}
              >
                <LogoutIcon />
                <span className="sr-only xl:not-sr-only">Выйти</span>
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div data-testid="app-shell-notices">
        <SupportAccessBanner />
        <OfflineStatusBanner />
        <ServerStatusBanner />
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
        <main
          id="main-content"
          tabIndex={-1}
          className="min-h-[calc(100dvh-var(--app-header-height))] min-w-0 px-3 py-4 sm:px-4 sm:py-5 xl:px-5"
        >
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

      {mfaStepUpRequested ? (
        <Suspense fallback={<MfaStepUpLoading />}>
          <MfaStepUpDialog />
        </Suspense>
      ) : null}
    </div>
  );
}

function MfaStepUpLoading(): JSX.Element {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="Загрузка подтверждения действия"
      aria-busy="true"
      tabIndex={-1}
      className="fixed inset-0 z-modal flex items-center justify-center bg-overlay p-4 outline-none"
    >
      <div className="rounded-lg border border-border bg-surface-raised px-5 py-4 text-sm text-foreground-muted shadow-xl">
        Загрузка защиты…
      </div>
    </div>
  );
}

function MenuIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

function CloseIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

function LogoutIcon(): JSX.Element {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}
