import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Modal({
  open,
  onClose,
  title,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
}): JSX.Element | null {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="bg-overlay fixed inset-0 z-modal flex items-center justify-center px-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={cn(
          // max-h + flex-column lets the body scroll on tall content
          // (e.g. AdminBillingDrawer's 3 stacked forms) while keeping
          // the header pinned to the top.
          "flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-border bg-surface-raised shadow-xl",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-foreground-muted transition-colors hover:bg-foreground/5 hover:text-foreground"
          >
            ✕
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  );
}
