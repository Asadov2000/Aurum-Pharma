import { forwardRef, useState } from "react";

import { Button } from "@/components/ui";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { cn } from "@/lib/utils";

export type ScannerStatus = "ready" | "scanning" | "off";

/**
 * Product search row for the cart's left column: a catalog typeahead, a qty
 * field, an add button, and a small scanner status chip. Adding clears the
 * picker so the cashier can immediately search the next item.
 */
export const SearchBar = forwardRef<
  HTMLInputElement,
  {
    onAdd: (catalogId: string, name: string, qty: number) => void;
    busy?: boolean;
    scanner?: ScannerStatus;
    touch?: boolean;
  }
>(function SearchBar({ onAdd, busy, scanner = "ready", touch }, ref) {
  const [catalogId, setCatalogId] = useState("");
  const [name, setName] = useState("");
  const [qty, setQty] = useState("1");

  const submit = () => {
    const q = Number(qty);
    if (!catalogId || q <= 0) return;
    onAdd(catalogId, name, q);
    setCatalogId("");
    setName("");
    setQty("1");
  };

  return (
    <div className="space-y-2 rounded-xl border border-border bg-surface p-3 shadow-sm">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <CatalogPicker
            ref={ref}
            value={catalogId}
            onChange={(id, brand) => {
              setCatalogId(id);
              setName(brand);
            }}
            placeholder="Поиск товара по названию…"
            clearable
          />
        </div>
        <input
          type="text"
          inputMode="numeric"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
          aria-label="Количество"
          className={cn(
            "rounded-md border border-input bg-surface px-2 text-center font-mono text-foreground",
            touch ? "h-14 w-16 text-xl" : "h-10 w-16",
          )}
        />
        <Button
          onClick={submit}
          isLoading={busy}
          disabled={!catalogId}
          size={touch ? "xl" : "md"}
        >
          Добавить
        </Button>
      </div>
      {scanner !== "off" && (
        <div className="flex items-center gap-2 text-xs text-foreground-muted">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              scanner === "scanning" ? "animate-pulse bg-info" : "bg-success",
            )}
            aria-hidden="true"
          />
          {scanner === "scanning" ? "Сканирование…" : "Сканер готов"}
        </div>
      )}
    </div>
  );
});
