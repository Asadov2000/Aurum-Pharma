import { lazy, Suspense, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "@tanstack/react-router";

import { useConnectivity } from "@/lib/connectivityContext";
import { cn } from "@/lib/utils";

import { BrandMark } from "./BrandMark";
import HeaderMenuContent from "./HeaderMenuContent";
import { HeaderLineIcon, NavIcon, NavigationSettingsIcon } from "./icons";
import { isActiveNavPath, type AppNavItem } from "./nav";

const AppearanceMenu = lazy(async () => {
  const module = await import("@/components/AppearanceMenu");
  return { default: module.AppearanceMenu };
});

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
    <header data-testid="app-shell-header" className="app-header">
      <div className="app-header-row">
        <div className="app-header-brand">
          <BrandMark tone="inverse" showName={props.sidebarExpanded} />
        </div>
        <div className="app-header-main">
          <button
            type="button"
            className="app-header-menu"
            aria-label="Открыть меню"
            aria-expanded={props.navigationOpen}
            onClick={(event) => props.onOpenNavigation(event.currentTarget)}
          >
            <MenuIcon />
          </button>
          <div className="app-header-context">
            {props.showPageTitle ? <h1 className="app-header-title">{props.pageTitle}</h1> : null}
            <span
              className={cn(
                "app-header-name",
                props.showPageTitle && "app-header-workspace-caption",
              )}
              title={props.workspaceLabel}
            >
              {props.workspaceLabel}
            </span>
            <span className="app-header-mobile-title">{props.pageTitle}</span>
            {!props.showPageTitle ? (
              <span className="app-header-desktop-caption">{props.pageTitle}</span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={props.onOpenSearch}
            aria-label="Перейти в раздел"
            aria-keyshortcuts="Control+k Meta+k"
            className="app-header-search"
          >
            <SearchIcon />
            <span className="hidden xl:inline">Перейти в раздел</span>
            <kbd className="app-header-shortcut">Ctrl K</kbd>
          </button>
          <ConnectionMenu />
          <Link
            to="/notifications"
            aria-label="Открыть уведомления"
            title="Уведомления"
            className="app-header-notifications"
          >
            <NavIcon to="/notifications" />
          </Link>
          <HeaderPopover
            key={`${props.name}:${props.pathname}`}
            title="Меню профиля"
            triggerClassName="app-header-profile"
            trigger={
              <>
                <span aria-hidden="true" className="app-header-avatar">
                  {props.name.charAt(0).toUpperCase()}
                </span>
                <span className="app-header-profile-label">
                  <span className="app-header-name">{props.name}</span>
                  <span className="app-header-caption">{props.caption}</span>
                </span>
              </>
            }
          >
            {(close) => (
              <HeaderMenuContent
                kind="profile"
                name={props.name}
                caption={props.caption}
                canOpenMyWork={props.canOpenMyWork}
                onLogout={props.onLogout}
                close={close}
              />
            )}
          </HeaderPopover>
        </div>
      </div>
      <div className="app-header-quick-row">
        <div className="app-header-quick-brand" />
        <nav aria-label="Быстрые переходы" className="app-header-quick-nav">
          <span className="app-header-quick-caption">
            {props.hasFavorites ? "Избранное" : "Быстрые переходы"}
          </span>
          <button
            type="button"
            aria-label="Перейти в раздел"
            onClick={props.onOpenSearch}
            className="app-header-mobile-search"
          >
            <SearchIcon />
          </button>
          <div className="aurum-scrollbar app-header-quick-scroll">
            {props.quickItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                aria-current={isActiveNavPath(props.pathname, item.to) ? "page" : undefined}
                className={cn(
                  "app-header-quick-link",
                  isActiveNavPath(props.pathname, item.to)
                    ? "bg-primary/10 text-primary"
                    : "app-header-quick-link-idle",
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
            className="app-header-settings"
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
      triggerClassName="app-header-connection"
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
      {(close) => <HeaderMenuContent kind="connection" close={close} />}
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
              className="app-header-popover"
              style={{ ...position, maxHeight: `calc(100dvh - ${position.top + 8}px)` }}
            >
              <div className="app-header-popover-heading">
                <h2 className="app-header-dialog-title">{title}</h2>
                <button
                  type="button"
                  aria-label={`Закрыть ${title.toLocaleLowerCase("ru")}`}
                  className="app-header-popover-close"
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
    <HeaderLineIcon size={18}>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </HeaderLineIcon>
  );
}
function MenuIcon(): JSX.Element {
  return (
    <HeaderLineIcon>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </HeaderLineIcon>
  );
}
