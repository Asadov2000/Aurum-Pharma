import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui";
import { useCatalogQuery } from "@/features/catalog/queries";

interface Props {
  value: string;
  onChange: (catalogId: string, brandName: string) => void;
  placeholder?: string;
  invalid?: boolean;
}

// Lightweight typeahead over /api/v1/catalog. Defers fetching until the
// user types something — a blank input does NOT spam the server.
export function CatalogPicker({
  value,
  onChange,
  placeholder = "Начните вводить название…",
  invalid = false,
}: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
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

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={text}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        placeholder={placeholder}
        invalid={invalid}
        autoComplete="off"
      />
      {value && !text && (
        <p className="mt-1 text-xs text-slate-500">
          Выбрано: <span className="font-mono">{value.slice(0, 8)}</span>
        </p>
      )}
      {open && debounced.length >= 2 && (
        <div className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {data?.items.length === 0 ? (
            <p className="px-3 py-2 text-sm italic text-slate-500">Ничего не найдено</p>
          ) : (
            data?.items.map((it) => (
              <button
                key={it.id}
                type="button"
                onClick={() => {
                  onChange(it.id, it.brand_name);
                  setText(it.brand_name);
                  setOpen(false);
                }}
                className="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50"
              >
                <span className="font-medium">{it.brand_name}</span>
                {it.dosage && <span className="ml-2 text-slate-500">{it.dosage}</span>}
                {it.manufacturer && (
                  <span className="ml-2 text-xs text-slate-400">· {it.manufacturer}</span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
