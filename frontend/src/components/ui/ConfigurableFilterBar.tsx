import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Button } from "./Button";
import { Checkbox } from "./Checkbox";
import { FilterBar } from "./FilterBar";

const STORAGE_PREFIX = "aurum:filter-layout:v1:";

export interface ConfigurableFilter {
  id: string;
  label: string;
  content: ReactNode;
  active: boolean;
  onClear: () => void;
  defaultVisible?: boolean;
  alwaysVisible?: boolean;
  available?: boolean;
}

interface ConfigurableFilterBarProps {
  preferenceKey: string;
  filters: readonly ConfigurableFilter[];
  onResetValues: () => void;
  className?: string;
  actions?: ReactNode;
}

function storageKey(preferenceKey: string): string {
  return `${STORAGE_PREFIX}${preferenceKey}`;
}

function defaultSelection(filters: readonly ConfigurableFilter[]): string[] {
  return filters
    .filter(
      (filter) => filter.available !== false && (filter.alwaysVisible || filter.defaultVisible),
    )
    .map((filter) => filter.id);
}

function readSelection(preferenceKey: string, filters: readonly ConfigurableFilter[]): string[] {
  const defaults = defaultSelection(filters);
  if (typeof window === "undefined") return defaults;

  try {
    const raw = window.localStorage.getItem(storageKey(preferenceKey));
    if (raw === null) return defaults;
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed) || !parsed.every((value) => typeof value === "string")) {
      return defaults;
    }
    const availableIds = new Set(
      filters.filter((filter) => filter.available !== false).map((filter) => filter.id),
    );
    return [...new Set(parsed)].filter((id) => availableIds.has(id));
  } catch {
    return defaults;
  }
}

function writeSelection(preferenceKey: string, ids: readonly string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(preferenceKey), JSON.stringify(ids));
  } catch {
    // The layout preference is optional; filtering must still work without storage.
  }
}

function hasStoredSelection(preferenceKey: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(storageKey(preferenceKey)) !== null;
  } catch {
    return false;
  }
}

export function ConfigurableFilterBar({ ...props }: ConfigurableFilterBarProps): JSX.Element {
  return <ConfigurableFilterBarState key={props.preferenceKey} {...props} />;
}

function ConfigurableFilterBarState({
  preferenceKey,
  filters,
  onResetValues,
  className,
  actions,
}: ConfigurableFilterBarProps): JSX.Element {
  const menuId = useId();
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const [menuOpen, setMenuOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>(() =>
    readSelection(preferenceKey, filters),
  );

  const availableFilters = filters.filter((filter) => filter.available !== false);
  const availableIdsSignature = availableFilters.map((filter) => filter.id).join("\u0000");
  const unavailableActiveSignature = filters
    .filter((filter) => filter.available === false && filter.active)
    .map((filter) => filter.id)
    .join("\u0000");
  const selectedSet = new Set(selectedIds);
  const visibleFilters = availableFilters.filter(
    (filter) => filter.alwaysVisible || selectedSet.has(filter.id),
  );
  const optionalFilters = availableFilters.filter((filter) => !filter.alwaysVisible);
  const activeCount = availableFilters.filter((filter) => filter.active).length;

  useEffect(() => {
    if (!unavailableActiveSignature) return;
    const unavailableActiveIds = new Set(unavailableActiveSignature.split("\u0000"));
    for (const filter of filtersRef.current) {
      if (unavailableActiveIds.has(filter.id) && filter.available === false && filter.active) {
        filter.onClear();
      }
    }
  }, [unavailableActiveSignature]);

  useEffect(() => {
    const availableIds = new Set(
      availableIdsSignature ? availableIdsSignature.split("\u0000") : [],
    );
    setSelectedIds((current) => {
      const next = current.filter((id) => availableIds.has(id));
      if (hasStoredSelection(preferenceKey)) writeSelection(preferenceKey, next);
      if (next.length === current.length) return current;
      return next;
    });
  }, [availableIdsSignature, preferenceKey]);

  useEffect(() => {
    if (!menuOpen) return undefined;

    requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLInputElement>('input[type="checkbox"]')?.focus();
    });

    const closeOnPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setMenuOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  const updateSelection = (next: string[]) => {
    setSelectedIds(next);
    writeSelection(preferenceKey, next);
  };

  const setVisible = (filter: ConfigurableFilter, visible: boolean) => {
    if (visible) {
      updateSelection([...selectedIds.filter((id) => id !== filter.id), filter.id]);
      return;
    }
    if (filter.active) filter.onClear();
    updateSelection(selectedIds.filter((id) => id !== filter.id));
  };

  const restoreDefaultLayout = () => {
    const next = defaultSelection(filters);
    const nextSet = new Set(next);
    for (const filter of availableFilters) {
      if (!nextSet.has(filter.id) && filter.active) filter.onClear();
    }
    updateSelection(next);
  };

  return (
    <FilterBar className={cn("relative grid grid-cols-1 sm:grid-cols-2 xl:flex", className)}>
      {visibleFilters.map((filter) => (
        <div
          key={filter.id}
          className={cn(
            "group flex min-w-0 items-end gap-1 [&>div]:min-w-0 [&>div]:flex-1 [&>div>div]:w-full",
            filter.alwaysVisible
              ? "sm:col-span-2 xl:flex-1 xl:basis-64"
              : "w-full xl:w-auto xl:[&>div]:flex-none",
          )}
          data-filter-id={filter.id}
        >
          <div className="min-w-0">{filter.content}</div>
          {!filter.alwaysVisible && (
            <Button
              variant="ghost"
              size="sm"
              className="h-9 w-9 shrink-0 px-0 text-lg text-foreground-muted"
              aria-label={`Убрать фильтр «${filter.label}»`}
              title={`Убрать фильтр «${filter.label}»`}
              onClick={() => {
                setVisible(filter, false);
                requestAnimationFrame(() => triggerRef.current?.focus());
              }}
            >
              <span aria-hidden="true">×</span>
            </Button>
          )}
        </div>
      ))}

      <div className="flex w-full shrink-0 items-end justify-end gap-2 border-t border-border pt-3 sm:col-span-2 xl:ml-auto xl:w-auto xl:border-0 xl:pt-0">
        {actions}
        {optionalFilters.length > 0 && (
          <div className="relative">
            <Button
              ref={triggerRef}
              variant="secondary"
              size="sm"
              aria-haspopup="dialog"
              aria-expanded={menuOpen}
              aria-controls={menuOpen ? menuId : undefined}
              onClick={() => setMenuOpen((open) => !open)}
            >
              Фильтры
              {activeCount > 0 && (
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1 text-xs text-primary">
                  {activeCount}
                </span>
              )}
            </Button>
            {menuOpen && (
              <div
                ref={menuRef}
                id={menuId}
                role="dialog"
                aria-label="Настройка фильтров"
                className="fixed inset-x-3 bottom-3 z-popover rounded-lg border border-border bg-surface-raised p-2 shadow-lg sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-2 sm:w-80"
              >
                <div className="flex items-center justify-between gap-3 px-2 pb-2">
                  <span className="text-sm font-semibold text-foreground">Показывать фильтры</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 px-0 text-lg"
                    aria-label="Закрыть настройку фильтров"
                    onClick={() => {
                      setMenuOpen(false);
                      triggerRef.current?.focus();
                    }}
                  >
                    <span aria-hidden="true">×</span>
                  </Button>
                </div>
                <div className="max-h-72 overflow-y-auto">
                  {optionalFilters.map((filter) => {
                    const checked = selectedSet.has(filter.id);
                    return (
                      <label
                        key={filter.id}
                        className="flex min-h-10 cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground hover:bg-foreground/5"
                      >
                        <Checkbox
                          checked={checked}
                          onChange={(event) => setVisible(filter, event.target.checked)}
                        />
                        <span className="min-w-0 flex-1">{filter.label}</span>
                        {filter.active && <span className="text-xs text-primary">применён</span>}
                      </label>
                    );
                  })}
                </div>
                <div className="mt-2 border-t border-border pt-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full justify-start"
                    onClick={restoreDefaultLayout}
                  >
                    Вернуть стандартный набор
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        <Button variant="ghost" size="sm" disabled={activeCount === 0} onClick={onResetValues}>
          Сбросить{activeCount > 0 ? ` (${activeCount})` : ""}
        </Button>
      </div>
    </FilterBar>
  );
}
