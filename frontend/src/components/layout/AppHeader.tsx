import { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "@tanstack/react-router";

import { useConnectivity } from "@/lib/connectivityContext";
import { cn } from "@/lib/utils";

import { BrandMark } from "./BrandMark";
import { NavIcon, NavigationSettingsIcon } from "./icons";
import { isActiveNavPath, type AppNavItem } from "./nav";

const AppearanceMenu = lazy(async () => {
  const module = await import("@/components/AppearanceMenu");
  return { default: module.AppearanceMenu };
});

const HeaderMenuContent = lazy(() => import("./HeaderMenuContent"));

interface AppHeaderProps {
  workspaceLabel: string;
  pageTitle: string;
  showPageTitle: boolean;
  name: string;
  caption: string;
  sidebarExpanded: boolean;
  quickItems: readonly AppNavItem[];
  hasFavorites: boolean;
  canOpenMyWork: boolean;
  pathname: string;
  navigationOpen: boolean;
  onOpenNavigation: (trigger: HTMLButtonElement) => void;
  onOpenSearch: () => void;
  onOpenSettings: () => void;
  onLogout: () => void;
}

export function AppHeader(props: AppHeaderProps): JSX.Element {
  return (
    <header
      data-testid="app-shell-header"
      className="sticky top-0 z-sticky border-b border-border bg-surface"
    >
      <div className="flex h-[var(--app-header-row-height)] items-stretch">
        <div className="hidden w-[var(--app-sidebar-width)] shrink-0 items-center border-r border-shell-sidebar-border bg-shell-sidebar px-4 lg:flex">
          <BrandMark tone="inverse" showName={props.sidebarExpanded} />
        </div>
        <div className="flex min-w-0 flex-1 items-center gap-1.5 px-3 sm:gap-3 sm:px-4 xl:px-5">
          <button
            type="button"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-md text-foreground-secondary hover:bg-foreground/5 lg:hidden"
            aria-label="Открыть меню"
            aria-expanded={props.navigationOpen}
            onClick={(event) => props.onOpenNavigation(event.currentTarget)}
          >
            <MenuIcon />
          </button>
          <div className="min-w-0 flex-1 leading-tight">
            {props.showPageTitle ? (
              <h1 className="hidden truncate font-display text-lg font-semibold text-foreground lg:block">
                {props.pageTitle}
              </h1>
            ) : null}
            <span
              className={cn(
                "block truncate text-sm font-semibold text-foreground",
                props.showPageTitle && "lg:text-xs lg:font-normal lg:text-foreground-muted",
              )}
              title={props.workspaceLabel}
            >
              {props.workspaceLabel}
            </span>
            <span className="block truncate text-xs text-foreground-muted lg:hidden">
              {props.pageTitle}
            </span>
            {!props.showPageTitle ? (
              <span className="hidden text-xs text-foreground-muted lg:block">{props.pageTitle}</span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={props.onOpenSearch}
            aria-label="Перейти в раздел"
            aria-keyshortcuts="Control+k Meta+k"
            className="hidden h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-foreground-muted transition-colors hover:border-primary/40 hover:text-primary md:inline-flex"
          >
            <SearchIcon />
            <span className="hidden xl:inline">Перейти в раздел</span>
            <kbd className="hidden rounded border border-border bg-surface px-1 text-[10px] xl:inline">
              Ctrl K
            </kbd>
          </button>
          <ConnectionMenu />
          <Link
            to="/notifications"
            aria-label="Открыть уведомления"
            title="Уведомления"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-md text-foreground-secondary hover:bg-foreground/5 hover:text-primary"
          >
            <NavIcon to="/notifications" />
          </Link>
          <HeaderPopover
            key={`${props.name}:${props.pathname}`}
            title="Меню профиля"
            triggerClassName="flex h-11 shrink-0 items-center gap-2 rounded-md px-1 text-foreground-secondary hover:bg-foreground/5 xl:px-2"
            trigger={
              <>
                <span
                  aria-hidden="true"
                  className="grid h-9 w-9 place-items-center rounded-full bg-primary/10 text-sm font-semibold text-primary"
                >
                  {props.name.charAt(0).toUpperCase()}
                </span>
                <span className="hidden max-w-40 text-left leading-tight xl:block">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {props.name}
                  </span>
                  <span className="block truncate text-xs text-foreground-muted">
                    {props.caption}
                  </span>
                </span>
              </>
            }
          >
            {(close) => (
              <Suspense
                fallback={
                  <p className="p-4 text-sm text-foreground-muted" role="status">
                    Загрузка меню…
                  </p>
                }
              >
                <HeaderMenuContent
                  kind="profile"
                  name={props.name}
                  caption={props.caption}
                  canOpenMyWork={props.canOpenMyWork}
                  onLogout={props.onLogout}
                  close={close}
                />
              </Suspense>
            )}
          </HeaderPopover>
        </div>
      </div>
      <div className="flex h-[var(--app-quick-links-height)] min-w-0 border-t border-border bg-background/60">
        <div className="hidden w-[var(--app-sidebar-width)] shrink-0 border-r border-shell-sidebar-border bg-shell-sidebar lg:block" />
        <nav
          aria-label="Быстрые переходы"
          className="flex min-w-0 flex-1 items-center gap-2 px-3 sm:px-4 xl:px-5"
        >
          <span className="hidden shrink-0 text-[11px] font-medium text-foreground-muted xl:inline">
            {props.hasFavorites ? "Избранное" : "Быстрые переходы"}
          </span>
          <button
            type="button"
            aria-label="Перейти в раздел"
            onClick={props.onOpenSearch}
            className="grid h-11 w-9 shrink-0 place-items-center rounded-md text-foreground-muted hover:text-primary md:hidden"
          >
            <SearchIcon />
          </button>
          <div className="aurum-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto overscroll-x-contain">
            {props.quickItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                aria-current={isActiveNavPath(props.pathname, item.to) ? "page" : undefined}
                className={cn(
                  "inline-flex min-h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 text-xs font-medium transition-colors",
                  isActiveNavPath(props.pathname, item.to)
                    ? "bg-primary/10 text-primary"
                    : "text-foreground-secondary hover:bg-foreground/5 hover:text-primary",
                )}
              >
                <NavIcon to={item.to} />
                {item.label}
              </Link>
            ))}
          </div>
          <div className="shrink-0">
            <Suspense fallback={<span className="block h-9 w-9" />}>
              <AppearanceMenu />
            </Suspense>
          </div>
          <button
            type="button"
            aria-label="Настроить быстрые переходы"
            title="Настроить быстрые переходы"
            onClick={props.onOpenSettings}
            className="grid h-11 w-9 shrink-0 place-items-center rounded-md text-foreground-muted hover:bg-foreground/5 hover:text-primary"
          >
            <NavigationSettingsIcon />
          </button>
        </nav>
      </div>
    </header>
  );
}

function ConnectionMenu(): JSX.Element {
  const { status } = useConnectivity();
  const label =
    status === "online"
      ? "Онлайн"
      : status === "offline"
        ? "Нет сети"
        : status === "server-unavailable"
          ? "Нет связи"
          : "Проверка";
  return (
    <HeaderPopover
      title="Состояние соединения"
      triggerClassName="flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border border-border bg-background px-2 text-[11px] font-medium text-foreground-secondary sm:px-3 sm:text-xs"
      trigger={
        <>
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 rounded-full",
              status === "online"
                ? "bg-success"
                : status === "checking"
                  ? "bg-warning"
                  : "bg-danger",
            )}
          />
          <span role="status" aria-live="polite">
            {label}
          </span>
        </>
      }
    >
      {(close) => (
        <Suspense
          fallback={
            <p className="p-4 text-sm text-foreground-muted" role="status">
              Загрузка меню…
            </p>
          }
        >
          <HeaderMenuContent kind="connection" close={close} />
        </Suspense>
      )}
    </HeaderPopover>
  );
}

function HeaderPopover({
  title,
  trigger,
  triggerClassName,
  children,
}: {
  title: string;
  trigger: ReactNode;
  triggerClassName: string;
  children: (close: () => void) => ReactNode;
}): JSX.Element {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("button, a[href]")?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !panelRef.current?.contains(event.target) &&
        !triggerRef.current?.contains(event.target)
      )
        setOpen(false);
    };
    const focusOutside = (event: FocusEvent) => {
      if (
        event.target instanceof Node &&
        !panelRef.current?.contains(event.target) &&
        !triggerRef.current?.contains(event.target)
      )
        setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") setOpen(false);
    };
    const resize = () => setOpen(false);
    const scroll = (event: Event) => {
      if (event.target instanceof Node && panelRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", focusOutside);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", resize);
    window.addEventListener("scroll", scroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", focusOutside);
      document.removeEventListener("keydown", key);
      window.removeEventListener("resize", resize);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [open]);
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={title}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        className={triggerClassName}
        onClick={() => {
          const rect = triggerRef.current?.getBoundingClientRect();
          if (rect)
            setPosition({
              left: Math.max(8, Math.min(rect.right - 304, window.innerWidth - 312)),
              top: rect.bottom + 8,
            });
          setOpen((previous) => !previous);
        }}
      >
        {trigger}
      </button>
      {open
        ? createPortal(
            <div
              ref={panelRef}
              id={id}
              role="dialog"
              aria-modal="false"
              aria-label={title}
              className="fixed z-popover w-[min(19rem,calc(100vw-1rem))] overflow-y-auto rounded-lg border border-border bg-surface-raised shadow-lg"
              style={{ ...position, maxHeight: `calc(100dvh - ${position.top + 8}px)` }}
            >
              <div className="flex items-center justify-between border-b border-border py-1 pl-4 pr-1">
                <h2 className="text-sm font-semibold text-foreground">{title}</h2>
                <button
                  type="button"
                  aria-label={`Закрыть ${title.toLocaleLowerCase("ru")}`}
                  className="grid h-11 w-11 place-items-center rounded-md text-foreground-muted hover:bg-foreground/5"
                  onClick={() => {
                    close();
                    triggerRef.current?.focus();
                  }}
                >
                  ×
                </button>
              </div>
              {children(close)}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </svg>
  );
}
function MenuIcon(): JSX.Element {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}
