import { forwardRef, useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui";

import { useCatalogQuery } from "./queries";

interface Props {
  value: string;
  onChange: (catalogId: string, brandName: string) => void;
  /** Optional initial label shown when `value` is preset (e.g. from URL state). */
  initialLabel?: string;
  /** Render an inline ✕ that clears the selection. Useful for filters. */
  clearable?: boolean;
  placeholder?: string;
  invalid?: boolean;
  /** POS speed-up: while the results dropdown is open, pressing 1–9 picks that
   *  result. Opt-in so name/code filters elsewhere keep digits as plain input. */
  selectByNumber?: boolean;
}

// Lightweight typeahead over /api/v1/catalog. Defers fetching until the
// user types something — a blank input does NOT spam the server. The
// forwarded ref points at the text input (POS focuses it via "/").
export const CatalogPicker = forwardRef<HTMLInputElement, Props>(function CatalogPicker(
  {
    value,
    onChange,
    initialLabel,
    clearable = false,
    placeholder = "Начните вводить название…",
    invalid = false,
    selectByNumber = false,
  },
  ref,
): JSX.Element {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initialLabel ?? "");
  const [debounced, setDebounced] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(text.trim()), 200);
    return () => clearTimeout(t);
  }, [text]);

  const { data } = useCatalogQuery(
    { q: debounced, page: 1, page_size: 10 },
    debounced.length >= 2,
  );

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

  return (
    <div ref={containerRef} className="relative">
      <Input
        ref={ref}
        value={text}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (!selectByNumber || !open) return;
          const items = data?.items;
          if (!items || items.length === 0) return;
          if (/^[1-9]$/.test(e.key)) {
            const n = Number(e.key);
            const it = n <= items.length ? items[n - 1] : undefined;
            if (it) {
              e.preventDefault();
              choose(it);
            }
          }
        }}
        placeholder={placeholder}
        invalid={invalid}
        autoComplete="off"
        className={clearable && value ? "pr-9" : undefined}
      />
      {clearable && value && (
        <button
          type="button"
          onClick={onClear}
          aria-label="Очистить"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-foreground-muted hover:bg-foreground/5 hover:text-foreground-secondary"
        >
          ✕
        </button>
      )}
      {value && !text && (
        <p className="mt-1 text-xs text-foreground-muted">
          Выбрано: <span className="font-mono">{value.slice(0, 8)}</span>
        </p>
      )}
      {open && debounced.length >= 2 && (
        <div className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-border bg-surface shadow-lg">
          {data?.items.length === 0 ? (
            <p className="px-3 py-2 text-sm italic text-foreground-muted">Ничего не найдено</p>
          ) : (
            data?.items.map((it, idx) => (
              <button
                key={it.id}
                type="button"
                onClick={() => choose(it)}
                className="block w-full px-3 py-2 text-left text-sm hover:bg-foreground/[0.03]"
              >
                {selectByNumber && idx < 9 && (
                  <span className="mr-2 font-mono tabular-nums text-foreground-muted">
                    {idx + 1}
                  </span>
                )}
                <span className="font-medium">{it.brand_name}</span>
                {it.dosage && <span className="ml-2 text-foreground-muted">{it.dosage}</span>}
                {it.manufacturer && (
                  <span className="ml-2 text-xs text-foreground-muted">· {it.manufacturer}</span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
});
