import { useEffect, useId, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");
function getFocusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(focusableSelector)).filter(
    (el) => !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true",
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  className,
  bodyClassName,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}): JSX.Element | null {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    previousActiveElement.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const onKey = (e: KeyboardEvent) => {
      const openDialogs = document.querySelectorAll<HTMLElement>("[data-aurum-modal]");
      if (openDialogs[openDialogs.length - 1] !== dialogRef.current) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }

      if (e.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;

      const focusable = getFocusableElements(dialog);
      if (focusable.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (!(active instanceof HTMLElement) || !dialog.contains(active)) {
        e.preventDefault();
        first?.focus();
        return;
      }

      if (e.shiftKey && active === first) {
        e.preventDefault();
        last?.focus();
        return;
      }

      if (!e.shiftKey && active === last) {
        e.preventDefault();
        first?.focus();
      }
    };

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    const dialog = dialogRef.current;
    const firstFocusable = dialog ? getFocusableElements(dialog)[0] : undefined;
    if (
      dialog &&
      (!(document.activeElement instanceof HTMLElement) || !dialog.contains(document.activeElement))
    ) {
      (firstFocusable ?? dialog).focus();
    }

    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKey);
      if (previousActiveElement.current && document.contains(previousActiveElement.current)) {
        previousActiveElement.current.focus();
      }
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="bg-overlay fixed inset-0 z-modal flex items-end justify-center sm:items-center sm:p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className={cn(
          // max-h + flex-column lets the body scroll on tall content
          // (e.g. AdminBillingDrawer's 3 stacked forms) while keeping
          // the header pinned to the top.
          "flex max-h-[calc(100dvh-1rem)] w-full max-w-lg flex-col rounded-t-lg border border-border bg-surface-raised shadow-xl sm:max-h-[calc(100dvh-2rem)] sm:rounded-lg",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-aurum-modal
        tabIndex={-1}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3.5 sm:px-5">
          <h2 id={titleId} className="min-w-0 truncate text-base font-semibold text-foreground">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="grid h-[var(--control-height-sm)] w-[var(--control-height-sm)] shrink-0 place-items-center rounded-md text-lg text-foreground-muted transition-colors duration-fast hover:bg-foreground/5 hover:text-foreground"
          >
            ✕
          </button>
        </div>
        <div className={cn("min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5", bodyClassName)}>
          {children}
        </div>
      </div>
    </div>
  );
}
