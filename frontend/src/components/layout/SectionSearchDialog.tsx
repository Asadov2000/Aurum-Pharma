import { useEffect, useId, useRef, useState } from "react";

import { Input, Modal } from "@/components/ui";

import { NavIcon } from "./icons";
import { type AppNavItem } from "./nav";
import { type AppRoutePath } from "./routeAccess";

interface SectionSearchDialogProps {
  open: boolean;
  items: readonly AppNavItem[];
  onClose: () => void;
  onSelect: (to: AppRoutePath) => void;
}

function normalizeSearch(value: string): string {
  return value.trim().toLocaleLowerCase("ru-RU").replaceAll("ё", "е");
}

export default function SectionSearchDialog({
  open,
  items,
  onClose,
  onSelect,
}: SectionSearchDialogProps): JSX.Element {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const searchId = useId();
  const normalizedQuery = normalizeSearch(query);
  const results = items.filter((item) => normalizeSearch(item.label).includes(normalizedQuery));

  useEffect(() => {
    if (open) inputRef.current?.focus();
    else setQuery("");
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Перейти в раздел"
      className="sm:max-w-xl"
      bodyClassName="flex min-h-0 flex-col overflow-hidden p-0 sm:p-0"
    >
      <div className="shrink-0 border-b border-border p-4 sm:px-5">
        <label htmlFor={searchId} className="sr-only">
          Найти раздел
        </label>
        <div className="relative">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            aria-hidden="true"
            focusable="false"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-foreground-muted"
          >
            <circle cx="10.5" cy="10.5" r="6.5" />
            <path d="m16 16 4.5 4.5" />
          </svg>
          <Input
            ref={inputRef}
            id={searchId}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Название раздела"
            autoComplete="off"
            className="pl-10"
          />
        </div>
      </div>
      <div className="min-h-0 overflow-y-auto p-2 sm:px-3">
        {results.length === 0 ? (
          <div role="status" className="px-4 py-10 text-center">
            <p className="text-sm font-medium text-foreground">Разделы не найдены</p>
            <p className="mt-1 text-xs text-foreground-muted">Попробуйте другое название</p>
          </div>
        ) : (
          <ul aria-label="Доступные разделы" className="space-y-1">
            {results.map((item) => (
              <li key={item.to}>
                <button
                  type="button"
                  onClick={() => onSelect(item.to)}
                  className="flex min-h-11 w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-sm font-medium text-foreground transition-colors duration-fast hover:bg-foreground/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
                >
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-surface text-foreground-muted">
                    <NavIcon to={item.to} />
                  </span>
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  <span aria-hidden="true" className="text-foreground-muted">
                    →
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
