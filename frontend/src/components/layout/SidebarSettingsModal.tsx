import { useMemo, useState } from "react";

import { Button, Checkbox, Input, Modal } from "@/components/ui";
import { cn } from "@/lib/utils";

import { NavIcon } from "./icons";
import { type NavItem } from "./Sidebar";
import {
  defaultSidebarPreferences,
  moveSidebarRoute,
  orderSidebarItems,
  SIDEBAR_SECTIONS,
  type SidebarPreferences,
  toggleSidebarFavorite,
  toggleSidebarRoute,
} from "./sidebarPreferences";

interface SidebarSettingsModalProps {
  open: boolean;
  items: readonly NavItem[];
  preferences: SidebarPreferences;
  activeRoute?: string;
  onChange: (preferences: SidebarPreferences) => void;
  onClose: () => void;
}

export function SidebarSettingsModal({
  open,
  items,
  preferences,
  activeRoute,
  onChange,
  onClose,
}: SidebarSettingsModalProps): JSX.Element | null {
  const [query, setQuery] = useState("");
  const availableRoutes = useMemo(() => items.map((item) => item.to), [items]);
  const ordered = useMemo(() => orderSidebarItems(items, preferences), [items, preferences]);
  const normalizedQuery = query.trim().toLocaleLowerCase("ru-RU");
  const filtered =
    normalizedQuery.length === 0
      ? ordered
      : ordered.filter((item) => item.label.toLocaleLowerCase("ru-RU").includes(normalizedQuery));
  const visibleCount = availableRoutes.filter(
    (route) => !preferences.hiddenRoutes.includes(route),
  ).length;
  const claimed = new Set(SIDEBAR_SECTIONS.flatMap((section) => section.routes));
  const groups = [
    ...SIDEBAR_SECTIONS.map((section) => ({
      ...section,
      items: filtered.filter((item) => section.routes.includes(item.to)),
    })),
    {
      id: "other",
      caption: "Другое",
      routes: filtered.filter((item) => !claimed.has(item.to)).map((item) => item.to),
      items: filtered.filter((item) => !claimed.has(item.to)),
    },
  ].filter((group) => group.items.length > 0);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Настроить меню"
      className="sm:max-w-2xl"
      bodyClassName="flex min-h-0 flex-col overflow-hidden p-0 sm:p-0"
    >
      <div className="shrink-0 border-b border-border px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-2 text-xs font-semibold text-foreground-muted">
              Вид на компьютере
            </div>
            <div
              role="group"
              aria-label="Вид боковой панели"
              className="grid w-full grid-cols-3 rounded-md border border-input bg-background p-0.5 sm:inline-flex sm:w-auto"
            >
              <ModeButton
                active={preferences.desktopMode === "auto"}
                label="Авто"
                shortLabel="Авто"
                icon={<AutoModeIcon />}
                onClick={() => onChange({ ...preferences, desktopMode: "auto" })}
              />
              <ModeButton
                active={preferences.desktopMode === "expanded"}
                label="Развёрнутый"
                shortLabel="Полный"
                icon={<ExpandedModeIcon />}
                onClick={() => onChange({ ...preferences, desktopMode: "expanded" })}
              />
              <ModeButton
                active={preferences.desktopMode === "compact"}
                label="Компактный"
                shortLabel="Узкий"
                icon={<CompactModeIcon />}
                onClick={() => onChange({ ...preferences, desktopMode: "compact" })}
              />
            </div>
          </div>

          <div className="w-full sm:max-w-64">
            <label htmlFor="sidebar-settings-search" className="sr-only">
              Найти раздел
            </label>
            <div className="relative">
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-muted"
              >
                <SearchIcon />
              </span>
              <Input
                id="sidebar-settings-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Найти раздел"
                className="pl-9"
                autoComplete="off"
              />
            </div>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 sm:px-5">
        {groups.length === 0 ? (
          <div className="py-10 text-center text-sm text-foreground-muted">Разделы не найдены</div>
        ) : (
          groups.map((group) => (
            <section key={group.id} aria-labelledby={`sidebar-group-${group.id}`} className="py-2">
              <div className="flex items-center justify-between gap-3 px-1 pb-1.5">
                <h3
                  id={`sidebar-group-${group.id}`}
                  className="text-xs font-semibold text-foreground-muted"
                >
                  {group.caption ?? "Главная"}
                </h3>
              </div>
              <div className="divide-y divide-border border-y border-border">
                {group.items.map((item) => {
                  const isHidden = preferences.hiddenRoutes.includes(item.to);
                  const isFavorite = preferences.favoriteRoutes.includes(item.to);
                  const isCurrent = item.to === activeRoute;
                  const sectionAvailableRoutes = group.routes.filter((route) =>
                    availableRoutes.includes(route),
                  );
                  const sectionOrder = ordered
                    .filter((candidate) => sectionAvailableRoutes.includes(candidate.to))
                    .map((candidate) => candidate.to);
                  const sectionIndex = sectionOrder.indexOf(item.to);
                  return (
                    <div
                      key={item.to}
                      className={cn(
                        "flex min-h-[var(--control-height-xl)] items-center gap-3 py-2",
                        isHidden && "text-foreground-muted",
                      )}
                    >
                      <Checkbox
                        checked={!isHidden}
                        disabled={isCurrent || (!isHidden && visibleCount <= 1)}
                        title={isCurrent ? "Открытый раздел нельзя скрыть" : undefined}
                        aria-label={`${isHidden ? "Показать" : "Скрыть"} раздел «${item.label}»`}
                        onChange={() =>
                          onChange(toggleSidebarRoute(preferences, item.to, availableRoutes))
                        }
                      />
                      <span
                        className={cn(
                          "grid h-9 w-9 shrink-0 place-items-center rounded-md bg-foreground/5 text-foreground-secondary",
                          !isHidden && "text-primary",
                        )}
                      >
                        <NavIcon to={item.to} />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {item.label}
                      </span>

                      <div className="flex shrink-0 items-center gap-0.5">
                        <IconButton
                          label={
                            isFavorite
                              ? `Убрать «${item.label}» из избранного`
                              : `Добавить «${item.label}» в избранное`
                          }
                          title={isFavorite ? "Убрать из избранного" : "Добавить в избранное"}
                          disabled={isHidden}
                          active={isFavorite}
                          onClick={() =>
                            onChange(toggleSidebarFavorite(preferences, item.to, availableRoutes))
                          }
                        >
                          <StarIcon filled={isFavorite} />
                        </IconButton>
                        <IconButton
                          label={`Поднять раздел «${item.label}»`}
                          title="Поднять"
                          disabled={normalizedQuery.length > 0 || sectionIndex <= 0}
                          onClick={() =>
                            onChange(
                              moveSidebarRoute(
                                preferences,
                                item.to,
                                -1,
                                availableRoutes,
                                sectionAvailableRoutes,
                              ),
                            )
                          }
                        >
                          <ArrowIcon direction="up" />
                        </IconButton>
                        <IconButton
                          label={`Опустить раздел «${item.label}»`}
                          title="Опустить"
                          disabled={
                            normalizedQuery.length > 0 ||
                            sectionIndex < 0 ||
                            sectionIndex >= sectionOrder.length - 1
                          }
                          onClick={() =>
                            onChange(
                              moveSidebarRoute(
                                preferences,
                                item.to,
                                1,
                                availableRoutes,
                                sectionAvailableRoutes,
                              ),
                            )
                          }
                        >
                          <ArrowIcon direction="down" />
                        </IconButton>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border px-4 py-3 sm:px-5">
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={preferences.hiddenRoutes.length === 0}
            onClick={() => onChange({ ...preferences, hiddenRoutes: [] })}
          >
            Показать все
          </Button>
          <Button variant="ghost" size="sm" onClick={() => onChange(defaultSidebarPreferences())}>
            Сбросить
          </Button>
        </div>
        <Button size="sm" onClick={onClose}>
          Готово
        </Button>
      </div>
    </Modal>
  );
}

function ModeButton({
  active,
  label,
  shortLabel,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  shortLabel: string;
  icon: JSX.Element;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-[var(--control-height-sm)] min-w-0 items-center justify-center gap-1.5 rounded px-1.5 text-xs font-medium transition-colors duration-fast sm:gap-2 sm:px-3 sm:text-sm",
        active
          ? "bg-surface text-primary shadow-sm"
          : "text-foreground-secondary hover:text-foreground",
      )}
    >
      {icon}
      <span className="sm:hidden">{shortLabel}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

function IconButton({
  label,
  title,
  active = false,
  disabled = false,
  onClick,
  children,
}: {
  label: string;
  title: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: JSX.Element;
}): JSX.Element {
  return (
    <button
      type="button"
      aria-label={label}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "grid h-9 w-9 place-items-center rounded-md text-foreground-muted transition-colors duration-fast hover:bg-foreground/5 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-30",
        active && "text-primary",
      )}
    >
      {children}
    </button>
  );
}

function SearchIcon(): JSX.Element {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </svg>
  );
}

function StarIcon({ filled }: { filled: boolean }): JSX.Element {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3Z" />
    </svg>
  );
}

function ArrowIcon({ direction }: { direction: "up" | "down" }): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={direction === "up" ? "m6 15 6-6 6 6" : "m6 9 6 6 6-6"} />
    </svg>
  );
}

function ExpandedModeIcon(): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16M12 9h6M12 13h6" />
    </svg>
  );
}

function CompactModeIcon(): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M8 4v16M5.5 9h.01M5.5 13h.01" />
    </svg>
  );
}

function AutoModeIcon(): JSX.Element {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="18" height="13" rx="2" />
      <path d="M8 21h8M12 18v3M8 9h8M8 13h5" />
    </svg>
  );
}
