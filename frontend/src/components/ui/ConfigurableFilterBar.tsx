import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Button } from "./Button";
import { FilterBar } from "./FilterBar";
import { Modal } from "./Modal";

export interface ConfigurableFilter {
  id: string;
  label: string;
  activeLabel?: string;
  content: ReactNode;
  active: boolean;
  onClear: () => void;
  /** Prioritize in the quick row (up to three fields); remaining conditions stay in the side panel. */
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
  pendingChangesMessage?: string;
}

export function ConfigurableFilterBar({ ...props }: ConfigurableFilterBarProps): JSX.Element {
  return <ConfigurableFilterBarState key={props.preferenceKey} {...props} />;
}

function ConfigurableFilterBarState({
  filters,
  onResetValues,
  className,
  actions,
  pendingChangesMessage,
}: ConfigurableFilterBarProps): JSX.Element {
  const panelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const [panelOpen, setPanelOpen] = useState(false);
  const availableFilters = filters.filter((filter) => filter.available !== false);
  const toolbarFilters = availableFilters.filter((filter) => filter.alwaysVisible);
  const optionalFilters = availableFilters.filter((filter) => !filter.alwaysVisible);
  const preferredFilters = optionalFilters.filter((filter) => filter.defaultVisible);
  const hasConfiguredQuickFilters = filters.some(
    (filter) => filter.defaultVisible && !filter.alwaysVisible,
  );
  const quickFilters = (hasConfiguredQuickFilters ? preferredFilters : optionalFilters).slice(0, 3);
  const quickFilterIds = new Set(quickFilters.map((filter) => filter.id));
  const advancedFilters = optionalFilters.filter((filter) => !quickFilterIds.has(filter.id));
  const activeFilters = availableFilters.filter((filter) => filter.active);
  const activeAdvancedCount = advancedFilters.filter((filter) => filter.active).length;
  const unavailableActiveSignature = filters
    .filter((filter) => filter.available === false && filter.active)
    .map((filter) => filter.id)
    .join("\u0000");

  useEffect(() => {
    if (!unavailableActiveSignature) return;
    const unavailableActiveIds = new Set(unavailableActiveSignature.split("\u0000"));
    for (const filter of filtersRef.current) {
      if (unavailableActiveIds.has(filter.id) && filter.available === false && filter.active) {
        filter.onClear();
      }
    }
  }, [unavailableActiveSignature]);

  const resetButton = (
    <Button
      variant="ghost"
      size="sm"
      className="min-h-11 shrink-0"
      disabled={activeFilters.length === 0}
      onClick={onResetValues}
    >
      Сбросить всё{activeFilters.length > 0 ? ` (${activeFilters.length})` : ""}
    </Button>
  );

  return (
    <FilterBar className={cn("flex-col items-stretch gap-0 p-0", className)}>
      <div ref={toolbarRef} className="w-full min-w-0">
        <div className="flex min-w-0 flex-col gap-3 rounded-t-lg border-b border-border bg-primary/5 px-4 py-3 sm:flex-row sm:items-end">
          {toolbarFilters.length > 0 ? (
            <div className="flex min-w-0 flex-1 flex-wrap items-end gap-3">
              <span className="w-full text-xs font-semibold uppercase tracking-widest text-primary">
                {toolbarFilters.some((filter) => /^(search|q|query)$/.test(filter.id))
                  ? "Быстрый поиск"
                  : "Основные условия"}
              </span>
              {toolbarFilters.map((filter) => (
                <div
                  key={filter.id}
                  className={cn(
                    "min-w-0 flex-1 basis-56 [&>div]:!w-full [&_input:not([type=checkbox]):not([type=radio])]:min-h-11 [&_input:not([type=checkbox]):not([type=radio])]:!w-full [&_select]:min-h-11 [&_select]:!w-full",
                    filter.id === "search" && "[&_label]:sr-only",
                  )}
                  data-filter-id={filter.id}
                >
                  {filter.content}
                </div>
              ))}
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <span className="text-xs font-semibold uppercase tracking-widest text-primary">
                Быстрый отбор
              </span>
            </div>
          )}
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {actions}
            {resetButton}
          </div>
        </div>

        {(quickFilters.length > 0 || advancedFilters.length > 0) && (
          <div className="flex min-w-0 flex-wrap items-end gap-3 px-4 py-3">
            {quickFilters.map((filter) => (
              <div
                key={filter.id}
                className="min-w-[145px] flex-1 basis-36 [&>div]:!w-full [&_input:not([type=checkbox]):not([type=radio])]:min-h-11 [&_input:not([type=checkbox]):not([type=radio])]:!w-full [&_select]:min-h-11 [&_select]:!w-full"
                data-filter-id={filter.id}
              >
                {filter.content}
              </div>
            ))}
            {advancedFilters.length > 0 && (
              <Button
                ref={triggerRef}
                variant="secondary"
                size="sm"
                className="min-h-11 shrink-0 border-primary/15 bg-primary/5 text-primary hover:bg-primary/10"
                aria-haspopup="dialog"
                aria-expanded={panelOpen}
                aria-controls={panelOpen ? panelId : undefined}
                onClick={() => setPanelOpen(true)}
              >
                Ещё условия
                {activeAdvancedCount > 0 && (
                  <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1 text-xs text-primary">
                    {activeAdvancedCount}
                  </span>
                )}
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path
                    d="m6 9 6 6 6-6"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </Button>
            )}
          </div>
        )}

        <div
          className="flex min-w-0 flex-wrap items-center gap-2 rounded-b-lg border-t border-border bg-primary/[0.025] px-4 py-2.5"
          aria-label="Выбранные условия"
        >
          <span className="mr-1 text-xs font-medium text-foreground-secondary">
            Активные фильтры
          </span>
          {activeFilters.length === 0 && (
            <span className="text-xs text-foreground-muted">Нет выбранных условий</span>
          )}
          {activeFilters.map((filter) => (
            <button
              key={filter.id}
              type="button"
              className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1.5 text-sm text-primary transition-colors hover:bg-primary/10 motion-reduce:transition-none sm:min-h-8"
              onClick={() => {
                filter.onClear();
                const field = [
                  ...(toolbarRef.current?.querySelectorAll<HTMLElement>("[data-filter-id]") ?? []),
                ]
                  .find((element) => element.dataset.filterId === filter.id)
                  ?.querySelector<HTMLElement>("input, select, button");
                (field ?? triggerRef.current)?.focus();
              }}
              aria-label={`Сбросить фильтр «${filter.label}»`}
              aria-describedby={`${panelId}-condition-${filter.id}`}
            >
              <span
                id={`${panelId}-condition-${filter.id}`}
                className="min-w-0 break-words text-left"
              >
                {filter.activeLabel ?? filter.label}
              </span>
              <span aria-hidden="true" className="shrink-0">
                ×
              </span>
            </button>
          ))}
        </div>
      </div>

      {pendingChangesMessage && (
        <p role="status" className="w-full px-4 py-2 text-sm text-foreground-secondary">
          {pendingChangesMessage}
        </p>
      )}

      <Modal
        id={panelId}
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        title="Фильтры"
        placement="side"
        footer={
          <div className="flex items-center justify-between gap-3">
            {resetButton}
            <Button className="min-h-11 flex-1 sm:flex-none" onClick={() => setPanelOpen(false)}>
              Готово
            </Button>
          </div>
        }
      >
        <div className="space-y-5">
          {advancedFilters.map((filter) => (
            <div
              key={filter.id}
              data-filter-id={filter.id}
              className="min-w-0 [&>div]:!w-full [&_input:not([type=checkbox]):not([type=radio])]:min-h-11 [&_input:not([type=checkbox]):not([type=radio])]:!w-full [&_input:not([type=checkbox]):not([type=radio])]:text-base [&_select]:min-h-11 [&_select]:!w-full [&_select]:text-base sm:[&_input:not([type=checkbox]):not([type=radio])]:text-sm sm:[&_select]:text-sm"
            >
              {filter.content}
            </div>
          ))}
        </div>
      </Modal>
    </FilterBar>
  );
}
