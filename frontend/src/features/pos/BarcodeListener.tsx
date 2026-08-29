import { useEffect, useRef } from "react";

import {
  DESKTOP_BARCODE_SCANNED_EVENT,
  normalizeDesktopBarcode,
  type DesktopBarcodeScanEvent,
} from "@/lib/desktopBridge";

import { emptyScanBuffer, feedScanKey } from "./barcodeScan";

const isEditable = (el: Element | null): boolean =>
  !!el &&
  (el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    (el as HTMLElement).isContentEditable);

const isProductSearch = (el: Element | null): boolean =>
  el instanceof HTMLInputElement && el.id === "pos-product-search";

/**
 * Global barcode capture for the register. A hidden sink input keeps focus so
 * scans land somewhere harmless; clicking anywhere that isn't an interactive
 * control (search/qty/payment/buttons/dialogs) returns focus to it. The window
 * keydown listener runs the timing heuristic and fires `onScan` on a real burst.
 * It stays out of the way while a real field holds focus, except for the product
 * search: scanners must keep working there without turning a scan's Enter into
 * a catalog selection as well.
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
      const productSearchFocused = isProductSearch(el);
      // Quantity, payment and other editable controls own all their keyboard
      // input. Product search is the sole exception because a scanner burst
      // commonly arrives while the cashier is already searching.
      if (isEditable(el) && el !== sinkRef.current && !productSearchFocused) return;
      const hadBufferedCharacters = bufRef.current.chars.length > 0;
      const { buf, code } = feedScanKey(bufRef.current, e.key, performance.now());
      bufRef.current = buf;
      if (e.key === "Enter" && hadBufferedCharacters && !productSearchFocused) {
        // A malformed, short or slow scanner burst must never fall through to
        // the POS Enter shortcut and accidentally start a payment.
        e.preventDefault();
      }
      if (code) {
        e.preventDefault();
        // The listener runs in capture phase. Stop the recognised terminator
        // before CatalogPicker can also treat it as a manual selection.
        e.stopImmediatePropagation();
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

    const onDesktopBarcode = (event: DesktopBarcodeScanEvent) => {
      const code = normalizeDesktopBarcode(event.detail.code);
      if (!code) return;
      onScanRef.current(code);
      sinkRef.current?.focus();
    };

    // Capture first so a completed scanner burst can reserve its Enter before
    // POS payment shortcuts observe the same keyboard event.
    window.addEventListener("keydown", onKey, { capture: true });
    window.addEventListener("click", onClick);
    window.addEventListener(DESKTOP_BARCODE_SCANNED_EVENT, onDesktopBarcode);
    return () => {
      window.removeEventListener("keydown", onKey, { capture: true });
      window.removeEventListener("click", onClick);
      window.removeEventListener(DESKTOP_BARCODE_SCANNED_EVENT, onDesktopBarcode);
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
