import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "⌫"];

/**
 * On-screen numeric keypad for touch mode. Default export so it can be
 * lazy-loaded (POS part 5) — it's only pulled in when a cashier taps a qty or
 * amount field. Renders its own scrim + role="dialog" so the global F-key
 * shortcuts stay suppressed while it's open. Physical keys work too.
 */
export default function NumPad({
  title,
  initial = "",
  allowDecimal = true,
  onSubmit,
  onClose,
}: {
  title: string;
  initial?: string;
  allowDecimal?: boolean;
  onSubmit: (value: string) => void;
  onClose: () => void;
}): JSX.Element {
  const [buf, setBuf] = useState(initial);
  const [pristine, setPristine] = useState(true);

  const press = (k: string) => {
    if (k === "⌫") {
      setBuf((b) => b.slice(0, -1));
      setPristine(false);
      return;
    }
    if (k === ".") {
      if (!allowDecimal) return;
      setBuf((b) => {
        const current = pristine ? "" : b;
        return current.includes(".") ? current : current === "" ? "0." : current + ".";
      });
      setPristine(false);
      return;
    }
    setBuf((b) => {
      const current = pristine ? "" : b;
      const next = current === "0" ? k : current + k;
      const pattern = allowDecimal ? /^\d{0,12}(?:\.\d{0,2})?$/ : /^\d{0,9}$/;
      return pattern.test(next) ? next : current;
    });
    setPristine(false);
  };

  const submit = () => {
    const v = buf.trim();
    const numeric = Number(v);
    if (v === "" || v === "." || !Number.isFinite(numeric) || numeric <= 0) return;
    onSubmit(v);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        submit();
        return;
      }
      if (e.key === "Backspace") {
        e.preventDefault();
        press("⌫");
        return;
      }
      if (e.key >= "0" && e.key <= "9") {
        e.preventDefault();
        press(e.key);
        return;
      }
      if (e.key === "." || e.key === ",") {
        e.preventDefault();
        press(".");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // submit/press close over `buf`/`allowDecimal`; re-bind each change.
  }, [buf, allowDecimal]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      className="bg-overlay fixed inset-0 z-modal flex items-center justify-center px-4"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xs rounded-lg border border-border bg-surface-raised p-4 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-medium text-foreground-secondary">{title}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-foreground-muted transition-colors hover:bg-foreground/5 hover:text-foreground"
          >
            ✕
          </button>
        </div>

        <div className="mb-3 flex h-14 items-center justify-end rounded-md border border-input bg-surface px-3 font-mono text-3xl tabular-nums text-foreground">
          {buf || "0"}
        </div>

        <div className="grid grid-cols-3 gap-2">
          {KEYS.map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => press(k)}
              disabled={k === "." && !allowDecimal}
              className={cn(
                "flex h-[60px] min-w-[60px] items-center justify-center rounded-lg border border-border bg-surface text-2xl font-medium text-foreground",
                "transition-colors active:bg-primary/10 disabled:opacity-30",
              )}
            >
              {k}
            </button>
          ))}
        </div>

        <Button size="xl" className="mt-3 w-full" onClick={submit}>
          ОК
        </Button>
      </div>
    </div>
  );
}
