import { forwardRef, useRef, useState } from "react";

import { Button, Label } from "@/components/ui";
import { CatalogPicker } from "@/features/catalog/CatalogPicker";
import { cn } from "@/lib/utils";

export type ScannerStatus = "ready" | "scanning" | "off";

/**
 * Primary POS search surface: a catalog typeahead, quantity, add action and
 * scanner status. Adding clears the picker so the next scan/search starts
 * immediately.
 */
export const SearchBar = forwardRef<
  HTMLInputElement,
  {
    onAdd: (
      catalogId: string,
      name: string,
      qty: number,
    ) => boolean | void | Promise<boolean | void>;
    busy?: boolean;
    scanner?: ScannerStatus;
    touch?: boolean;
    branchId?: string;
  }
>(function SearchBar({ onAdd, busy, scanner = "ready", touch, branchId }, ref) {
  const [catalogId, setCatalogId] = useState("");
  const [name, setName] = useState("");
  const [qty, setQty] = useState("1");
  // Remounting the picker after an add clears its text → next search starts
  // fresh and the empty field lets the global Enter shortcut pay (see SaleArea).
  const [pickerKey, setPickerKey] = useState(0);
  const qtyRef = useRef<HTMLInputElement>(null);
  const submitLockRef = useRef(false);

  const submit = async () => {
    const q = Number(qty);
    if (!catalogId || q <= 0 || submitLockRef.current) return;
    submitLockRef.current = true;
    try {
      const accepted = await onAdd(catalogId, name, q);
      if (accepted === false) return;
      setCatalogId("");
      setName("");
      setQty("1");
      setPickerKey((k) => k + 1);
    } finally {
      submitLockRef.current = false;
    }
  };

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-[4rem_minmax(0,1fr)] items-center gap-2 rounded-lg border-2 border-primary bg-surface p-2 shadow-sm sm:grid-cols-[minmax(0,1fr)_4rem_auto]">
        <div className="relative col-span-2 min-w-0 sm:col-span-1">
          <Label htmlFor="pos-product-search" className="sr-only">
            Товар
          </Label>
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 text-foreground-muted"
          >
            <SearchIcon />
          </span>
          <CatalogPicker
            key={pickerKey}
            id="pos-product-search"
            ref={ref}
            value={catalogId}
            onChange={(id, brand) => {
              setCatalogId(id);
              setName(brand);
              // Picking a product jumps straight to the quantity field, so the
              // cashier goes name → choose → number → Enter without the mouse.
              if (id) {
                qtyRef.current?.focus();
                qtyRef.current?.select();
              }
            }}
            placeholder="Найти товар или отсканировать штрих-код"
            clearable
            branchId={branchId}
            inputClassName={cn(
              "h-14 border-transparent bg-transparent pl-11 text-lg shadow-none hover:border-transparent focus:border-transparent focus:bg-transparent",
              touch && "h-16 text-xl",
            )}
          />
        </div>
        <input
          ref={qtyRef}
          type="text"
          inputMode="numeric"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void submit();
            }
          }}
          aria-label="Количество"
          className={cn(
            "rounded-md border border-input bg-surface px-2 text-center font-mono text-foreground",
            touch ? "h-16 w-16 text-xl" : "h-14 w-16 text-lg",
          )}
        />
        <Button
          onClick={() => void submit()}
          isLoading={busy}
          disabled={!catalogId}
          size="xl"
          className={cn("w-full sm:w-auto", touch && "h-16")}
        >
          Добавить
        </Button>
      </div>
      {scanner !== "off" && (
        <div className="flex items-center gap-2 px-1 text-xs text-foreground-muted">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              scanner === "scanning" ? "bg-info" : "bg-success",
            )}
            aria-hidden="true"
          />
          {scanner === "scanning" ? "Сканирование…" : "Сканер готов"}
        </div>
      )}
    </div>
  );
});

function SearchIcon(): JSX.Element {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </svg>
  );
}
