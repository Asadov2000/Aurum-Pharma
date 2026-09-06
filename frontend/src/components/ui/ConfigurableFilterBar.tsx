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
  /** @deprecated All available conditions are now shown in the filter panel. */
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
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const [panelOpen, setPanelOpen] = useState(false);
  const availableFilters = filters.filter((filter) => filter.available !== false);
  const toolbarFilters = availableFilters.filter((filter) => filter.alwaysVisible);
  const optionalFilters = availableFilters.filter((filter) => !filter.alwaysVisible);
  const activeFilters = availableFilters.filter((filter) => filter.active);
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
      className="min-h-11 sm:min-h-0"
      disabled={activeFilters.length === 0}
      onClick={onResetValues}
    >
      Сбросить{activeFilters.length > 0 ? ` (${activeFilters.length})` : ""}
    </Button>
  );

  return (
    <FilterBar className={cn("flex-col items-stretch", className)}>
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        {toolbarFilters.map((filter) => (
          <div
            key={filter.id}
            className="min-w-0 flex-1 basis-64 [&>div]:w-full [&_input]:min-h-11 [&_input]:text-base sm:[&_input]:min-h-0 sm:[&_input]:text-sm"
            data-filter-id={filter.id}
          >
            {filter.content}
          </div>
        ))}
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {actions}
          {optionalFilters.length > 0 && (
            <Button
              ref={triggerRef}
              variant="secondary"
              size="sm"
              className="min-h-11 sm:min-h-0"
              aria-haspopup="dialog"
              aria-expanded={panelOpen}
              aria-controls={panelOpen ? panelId : undefined}
              onClick={() => setPanelOpen(true)}
            >
              Фильтры
              {activeFilters.length > 0 && (
                <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1 text-xs text-primary">
                  {activeFilters.length}
                </span>
              )}
            </Button>
          )}
          {activeFilters.length === 0 && resetButton}
        </div>
      </div>

      {activeFilters.length > 0 && (
        <div
          className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border pt-3"
          aria-label="Выбранные условия"
        >
          {activeFilters.map((filter) => (
            <button
              key={filter.id}
              type="button"
              className="inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1.5 text-sm text-primary transition-colors hover:bg-primary/10 motion-reduce:transition-none sm:min-h-8"
              onClick={() => {
                filter.onClear();
                triggerRef.current?.focus();
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
          {resetButton}
        </div>
      )}

      {pendingChangesMessage && (
        <p role="status" className="text-sm text-foreground-secondary">
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
          {optionalFilters.map((filter) => (
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
