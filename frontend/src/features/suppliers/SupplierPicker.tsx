import { useEffect, useId, useMemo, useRef, useState } from "react";

import { Input } from "@/components/ui";
import { cn } from "@/lib/utils";

import { useSupplierOptionsQuery } from "./queries";
import { type SupplierOption } from "./types";

interface SupplierPickerProps {
  id?: string;
  value: string;
  onChange: (supplierId: string, supplierName: string) => void;
  initialLabel?: string;
  placeholder?: string;
  invalid?: boolean;
  clearable?: boolean;
  includeInactive?: boolean;
  className?: string;
}

export function SupplierPicker({
  id,
  value,
  onChange,
  initialLabel,
  placeholder = "Найти поставщика…",
  invalid = false,
  clearable = false,
  includeInactive = false,
  className,
}: SupplierPickerProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initialLabel ?? "");
  const [debounced, setDebounced] = useState("");
  const [highlight, setHighlight] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const internalValueClearRef = useRef(false);
  const previousValueRef = useRef(value);
  const listId = useId();

  useEffect(() => {
    const timeout = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(timeout);
  }, [text]);

  const query = useSupplierOptionsQuery(
    {
      q: debounced || undefined,
      include_inactive: includeInactive,
      selected_id: value || undefined,
      limit: 20,
    },
    open || Boolean(value && !text),
  );
  const items = useMemo(() => query.data?.items ?? [], [query.data?.items]);
  const resultsMatchInput = text.trim() === debounced;
  const selectableItems = resultsMatchInput ? items : [];

  useEffect(() => {
    if (previousValueRef.current === value) return;
    previousValueRef.current = value;
    if (value) {
      setText(initialLabel ?? "");
      setDebounced("");
      setOpen(false);
    }
  }, [initialLabel, value]);

  useEffect(() => {
    if (!value || text) return;
    const selected = items.find((item) => item.id === value);
    if (selected) setText(selected.name);
  }, [items, text, value]);

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

  useEffect(() => setHighlight(0), [debounced]);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!(event.target instanceof Node) || containerRef.current?.contains(event.target)) return;
      setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const choose = (supplier: SupplierOption) => {
    onChange(supplier.id, supplier.name);
    setText(supplier.name);
    setOpen(false);
  };

  const clear = () => {
    setText("");
    setDebounced("");
    onChange("", "");
  };

  const listOpen = open;

  return (
    <div ref={containerRef} className={cn("relative", className)}>
      <Input
        id={id}
        value={text}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          if (value) {
            internalValueClearRef.current = true;
            onChange("", "");
          }
          setText(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && listOpen) {
            event.preventDefault();
            setOpen(false);
            return;
          }
          if (!listOpen || selectableItems.length === 0 || query.isFetching) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setHighlight((current) => Math.min(current + 1, selectableItems.length - 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setHighlight((current) => Math.max(current - 1, 0));
          } else if (event.key === "Enter") {
            event.preventDefault();
            const selected = selectableItems[highlight] ?? selectableItems[0];
            if (selected) choose(selected);
          }
        }}
        placeholder={placeholder}
        invalid={invalid}
        autoComplete="off"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={listOpen}
        aria-busy={query.isFetching || undefined}
        aria-controls={listOpen ? listId : undefined}
        aria-activedescendant={
          listOpen && selectableItems[highlight]
            ? `${listId}-${selectableItems[highlight]?.id}`
            : undefined
        }
        className={cn(clearable && value && "pr-11")}
      />

      {clearable && value && (
        <button
          type="button"
          onClick={clear}
          aria-label="Очистить поставщика"
          className="absolute right-0 top-1/2 grid h-11 w-11 -translate-y-1/2 place-items-center rounded-md text-foreground-muted hover:bg-foreground/5 hover:text-foreground"
        >
          ×
        </button>
      )}

      {listOpen && (
        <div
          id={listId}
          role="listbox"
          aria-label="Результаты поиска поставщиков"
          className="absolute z-dropdown mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-surface-raised shadow-lg"
        >
          {!resultsMatchInput || (query.isFetching && !query.data) ? (
            <p className="px-3 py-3 text-sm text-foreground-muted" role="status">
              Поиск…
            </p>
          ) : query.error ? (
            <div className="px-3 py-3 text-sm text-danger-foreground" role="alert">
              <p>Не удалось загрузить поставщиков</p>
              <button
                type="button"
                className="mt-2 min-h-11 rounded-md border border-border px-3 font-medium"
                onClick={() => void query.refetch()}
              >
                Повторить
              </button>
            </div>
          ) : selectableItems.length === 0 ? (
            <p className="px-3 py-3 text-sm italic text-foreground-muted">Поставщики не найдены</p>
          ) : (
            selectableItems.map((supplier, index) => (
              <button
                key={supplier.id}
                id={`${listId}-${supplier.id}`}
                type="button"
                role="option"
                aria-selected={supplier.id === value}
                tabIndex={-1}
                onPointerEnter={() => setHighlight(index)}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => choose(supplier)}
                className={cn(
                  "flex min-h-11 w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm",
                  index === highlight ? "bg-foreground/[0.06]" : "hover:bg-foreground/[0.03]",
                )}
              >
                <span className="min-w-0 truncate font-medium">{supplier.name}</span>
                {!supplier.is_active && (
                  <span className="shrink-0 text-xs text-foreground-muted">Неактивен</span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
