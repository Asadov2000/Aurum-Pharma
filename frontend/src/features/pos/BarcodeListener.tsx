import { useEffect, useRef } from "react";

import { emptyScanBuffer, feedScanKey } from "./barcodeScan";

const isEditable = (el: Element | null): boolean =>
  !!el &&
  (el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    (el as HTMLElement).isContentEditable);

/**
 * Global barcode capture for the register. A hidden sink input keeps focus so
 * scans land somewhere harmless; clicking anywhere that isn't an interactive
 * control (search/qty/payment/buttons/dialogs) returns focus to it. The window
 * keydown listener runs the timing heuristic and fires `onScan` on a real burst
 * — and stays out of the way while a real field holds focus.
 */
export function BarcodeListener({
  enabled,
  onScan,
}: {
  enabled: boolean;
  onScan: (code: string) => void;
}): JSX.Element | null {
  const sinkRef = useRef<HTMLInputElement>(null);
  const bufRef = useRef(emptyScanBuffer());
  const onScanRef = useRef(onScan);
  onScanRef.current = onScan;

  useEffect(() => {
    if (!enabled) return;
    sinkRef.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      // Don't capture while a real field (search/qty/payment) is focused.
      if (isEditable(el) && el !== sinkRef.current) return;
      const { buf, code } = feedScanKey(bufRef.current, e.key, performance.now());
      bufRef.current = buf;
      if (code) {
        e.preventDefault();
        onScanRef.current(code);
      }
    };

    const onClick = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (t.closest('input,textarea,select,button,a,[role="dialog"],[contenteditable="true"]')) {
        return;
      }
      sinkRef.current?.focus();
    };

    window.addEventListener("keydown", onKey);
    window.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("click", onClick);
    };
  }, [enabled]);

  if (!enabled) return null;
  return (
    <input
      ref={sinkRef}
      data-barcode-sink="true"
      aria-hidden="true"
      tabIndex={-1}
      readOnly
      value=""
      className="pointer-events-none absolute h-0 w-0 opacity-0"
    />
  );
}
