import { type ReactNode, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useAuth } from "@/features/auth/hooks";
import { MfaStepUpDialog } from "@/features/auth/MfaStepUpDialog";

import { OfflineStatusBanner } from "./OfflineStatusBanner";
import { PwaInstallButton } from "./PwaInstallButton";
import { PwaUpdateBanner } from "./PwaUpdateBanner";
import { RuntimeSurfaceBadge } from "./RuntimeSurfaceBadge";
import { ServerStatusBanner } from "./ServerStatusBanner";
import { Sidebar } from "./Sidebar";
import { buildNav } from "./nav";

export function AppLayout({ children }: { children: ReactNode }): JSX.Element {
  const { user, logout } = useAuth();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const mobileDrawerRef = useRef<HTMLDivElement>(null);
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null);
  const isSupport = Boolean(user?.is_developer || user?.is_administrator);
  const hasTenant = Boolean(user?.home_tenant_id);
  const isTenantOwner = Boolean(user?.is_tenant_owner);
  const perms = user?.permissions ?? [];
  const items = buildNav(isSupport, hasTenant, isTenantOwner, perms, user?.is_developer === true);

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

  useEffect(() => {
    if (!mobileNavOpen) return undefined;

    const previousActiveElement =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const mobileMenuButton = mobileMenuButtonRef.current;
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
        setMobileNavOpen(false);
        return;
      }
      if (event.key !== "Tab") {
        return;
      }

      const drawer = mobileDrawerRef.current;
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
      mobileCloseButtonRef.current?.focus();
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
        mobileMenuButton?.focus();
      }
    };
  }, [mobileNavOpen]);

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[240px_minmax(0,1fr)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-toast focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-primary-foreground"
      >
        Перейти к содержимому
      </a>
      <div className="hidden lg:block">
        <Sidebar items={items} />
      </div>
      {mobileNavOpen && (
        <div className="fixed inset-0 z-overlay lg:hidden" role="presentation">
          <button
            type="button"
            aria-label="Закрыть меню через фон"
            className="absolute inset-0 bg-overlay"
            onClick={() => setMobileNavOpen(false)}
          />
          <div
            ref={mobileDrawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Меню приложения"
            className="relative z-modal h-full w-[min(20rem,calc(100vw-2rem))]"
          >
            <Sidebar
              items={items}
              mode="drawer"
              onNavigate={() => setMobileNavOpen(false)}
              closeButton={
                <Button
                  ref={mobileCloseButtonRef}
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 px-0"
                  aria-label="Закрыть меню"
                  onClick={() => setMobileNavOpen(false)}
                >
                  <CloseIcon />
                </Button>
              }
            />
          </div>
        </div>
      )}
      <div className="flex min-w-0 flex-col">
        <div className="sticky top-0 z-sticky">
          <header className="flex items-center justify-between gap-3 border-b border-border bg-surface/95 px-4 py-3 backdrop-blur supports-[backdrop-filter]:bg-surface/85 sm:px-6">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <Button
                ref={mobileMenuButtonRef}
                variant="secondary"
                size="sm"
                className="h-9 w-9 shrink-0 px-0 lg:hidden"
                aria-label="Открыть меню"
                onClick={() => setMobileNavOpen(true)}
              >
                <MenuIcon />
              </Button>
              <span
                aria-hidden="true"
                className="hidden h-9 w-9 shrink-0 place-items-center rounded-full bg-primary/10 font-display text-sm font-semibold text-primary sm:grid"
              >
                {initial}
              </span>
              <div className="hidden min-w-0 leading-tight sm:block">
                <div className="truncate text-sm font-semibold text-foreground">{name}</div>
                {caption && <div className="truncate text-xs text-foreground-muted">{caption}</div>}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2 sm:gap-3">
              <PwaInstallButton />
              <RuntimeSurfaceBadge />
              <ThemeToggle />
              <Button
                variant="secondary"
                size="sm"
                className="h-8 w-8 px-0 sm:w-auto sm:px-3"
                onClick={() => void logout()}
              >
                <LogoutIcon />
                <span className="sr-only sm:not-sr-only">Выйти</span>
              </Button>
            </div>
          </header>
          <OfflineStatusBanner />
          <ServerStatusBanner />
          <PwaUpdateBanner />
        </div>
        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 px-4 py-4 sm:px-6 sm:py-6">
          {children}
        </main>
      </div>
      <MfaStepUpDialog />
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
