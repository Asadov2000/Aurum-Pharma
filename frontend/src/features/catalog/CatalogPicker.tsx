import { forwardRef, useEffect, useId, useRef, useState } from "react";

import { Input } from "@/components/ui";
import { cn } from "@/lib/utils";

import { useCatalogPickerQuery } from "./pickerQueries";
import { type CatalogItem } from "./types";

interface Props {
  id?: string;
  value: string;
  onChange: (catalogId: string, brandName: string) => void;
  /** Optional initial label shown when `value` is preset (e.g. from URL state). */
  initialLabel?: string;
  /** Render an inline ✕ that clears the selection. Useful for filters. */
  clearable?: boolean;
  placeholder?: string;
  invalid?: boolean;
  inputClassName?: string;
  /** When set (POS register's branch), each result shows its available stock. */
  branchId?: string;
  favoriteCatalogIds?: ReadonlySet<string>;
  favoritePendingId?: string | null;
  onFavoriteToggle?: (item: CatalogItem) => void;
}

// Lightweight typeahead over /api/v1/catalog. Defers fetching until the
// user types something — a blank input does NOT spam the server. The
// forwarded ref points at the text input (POS focuses it via "/").
//
// Keyboard: digits are plain input (so dosages like "500" type normally).
// Selection is standard typeahead — ↑/↓ move the highlight (first result is
// highlighted by default), Enter picks the highlighted result.
export const CatalogPicker = forwardRef<HTMLInputElement, Props>(function CatalogPicker(
  {
    value,
    id,
    onChange,
    initialLabel,
    clearable = false,
    placeholder = "Начните вводить название…",
    invalid = false,
    inputClassName,
    branchId,
    favoriteCatalogIds,
    favoritePendingId,
    onFavoriteToggle,
  },
  ref,
): JSX.Element {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initialLabel ?? "");
  const [debounced, setDebounced] = useState("");
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const internalValueClearRef = useRef(false);
  const listId = useId();

  useEffect(() => {
    if (value) return;
    if (internalValueClearRef.current) {
      internalValueClearRef.current = false;
      return;
    }
    setText("");
    setDebounced("");
    setOpen(false);
  }, [value]);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);

  const {
    data,
    isFetching = false,
    isPlaceholderData = false,
    error = null,
  } = useCatalogPickerQuery(
    { q: debounced, page: 1, page_size: 10, branch_id: branchId },
    debounced.length >= 2,
  );

  // keepPreviousData is useful on full tables, but unsafe in a typeahead:
  // results for the previous query must never be selectable as the new query.
  const items = isPlaceholderData ? [] : (data?.items ?? []);

  // New results → highlight the first one again (set up the "type → Enter" flow).
  useEffect(() => {
    setHighlight(0);
  }, [debounced]);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClickOutside);
    return () => window.removeEventListener("mousedown", onClickOutside);
  }, []);

  const onClear = () => {
    setText("");
    setDebounced("");
    onChange("", "");
  };

  const choose = (it: { id: string; brand_name: string }) => {
    onChange(it.id, it.brand_name);
    setText(it.brand_name);
    setOpen(false);
  };

  const listOpen = open && debounced.length >= 2;

  return (
    <div ref={containerRef} className="relative">
      <Input
        id={id}
        ref={ref}
        value={text}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          if (value) {
            internalValueClearRef.current = true;
            onChange("", "");
          }
          setText(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape" && listOpen) {
            e.preventDefault();
            setOpen(false);
            return;
          }
          if (!listOpen || items.length === 0) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setHighlight((h) => Math.min(h + 1, items.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setHighlight((h) => Math.max(h - 1, 0));
          } else if (e.key === "Enter") {
            const it = items[highlight] ?? items[0];
            if (it) {
              e.preventDefault();
              choose(it);
            }
          }
        }}
        placeholder={placeholder}
        invalid={invalid}
        autoComplete="off"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={listOpen}
        aria-busy={isFetching || undefined}
        aria-controls={listOpen ? listId : undefined}
        aria-activedescendant={
          listOpen && items[highlight] ? `${listId}-${items[highlight]?.id}` : undefined
        }
        className={cn(inputClassName, clearable && value && "pr-11")}
      />
      {clearable && value && (
        <button
          type="button"
          onClick={onClear}
          aria-label="Очистить"
          className="absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-md text-foreground-muted hover:bg-foreground/5 hover:text-foreground-secondary"
        >
          ✕
        </button>
      )}
      {value && !text && (
        <p className="mt-1 text-xs text-foreground-muted">
          Выбрано: <span className="font-mono">{value.slice(0, 8)}</span>
        </p>
      )}
      {listOpen && (
        <div
          id={listId}
          role="listbox"
          className="absolute z-dropdown mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-surface-raised shadow-lg"
        >
          {isFetching ? (
            <p className="px-3 py-2 text-sm text-foreground-muted" role="status">
              Поиск…
            </p>
          ) : error ? (
            <p className="px-3 py-2 text-sm text-danger-foreground" role="alert">
              Не удалось выполнить поиск
            </p>
          ) : items.length === 0 ? (
            <p className="px-3 py-2 text-sm italic text-foreground-muted">Ничего не найдено</p>
          ) : (
            items.map((it, idx) => (
              <div
                key={it.id}
                onMouseEnter={() => setHighlight(idx)}
                className={
                  "flex w-full items-stretch " +
                  (idx === highlight ? "bg-foreground/[0.06]" : "hover:bg-foreground/[0.03]")
                }
              >
                <button
                  id={`${listId}-${it.id}`}
                  type="button"
                  role="option"
                  aria-selected={idx === highlight}
                  tabIndex={-1}
                  data-active={idx === highlight ? "true" : undefined}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(it)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-3 px-3 py-2 text-left text-sm"
                >
                  <span className="min-w-0 truncate">
                    <span className="font-medium">{it.brand_name}</span>
                    {it.dosage && <span className="ml-2 text-foreground-muted">{it.dosage}</span>}
                    {it.manufacturer && (
                      <span className="ml-2 text-xs text-foreground-muted">
                        · {it.manufacturer}
                      </span>
                    )}
                  </span>
                  {branchId && it.stock_available != null && (
                    <span
                      className={
                        "shrink-0 font-mono tabular-nums text-xs " +
                        (Number(it.stock_available) <= 0 ? "text-danger" : "text-foreground-muted")
                      }
                    >
                      {Number(it.stock_available) <= 0 ? "нет" : `${Number(it.stock_available)} шт`}
                    </span>
                  )}
                </button>
                {onFavoriteToggle ? (
                  <button
                    type="button"
                    aria-label={
                      favoriteCatalogIds?.has(it.id)
                        ? `Убрать ${it.brand_name} из избранного`
                        : `Добавить ${it.brand_name} в избранное`
                    }
                    aria-pressed={favoriteCatalogIds?.has(it.id) ?? false}
                    title={favoriteCatalogIds?.has(it.id) ? "Убрать из избранного" : "В избранное"}
                    disabled={favoritePendingId === it.id}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => onFavoriteToggle(it)}
                    className="grid min-h-11 w-11 shrink-0 place-items-center border-l border-border text-foreground-muted hover:text-warning disabled:opacity-50"
                  >
                    <StarIcon filled={favoriteCatalogIds?.has(it.id) ?? false} />
                  </button>
                ) : null}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
});

function StarIcon({ filled }: { filled: boolean }): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinejoin="round"
    >
      <path d="m12 2.8 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.4 6.3-.9z" />
    </svg>
  );
}
